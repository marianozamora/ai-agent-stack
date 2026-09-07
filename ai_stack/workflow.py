"""Reusable validator configuration and task metric aggregation (stdlib only)."""
import hashlib
import math
import os
import re
import signal
import statistics
import subprocess


def execute(command, cwd, env, output, timeout):
    """Terminate the validator's process group on timeout, including child tools."""
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as exc:
        output.write(str(exc))
        return 127
    try:
        return process.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        output.write(str(exc))
        return 130 if isinstance(exc, KeyboardInterrupt) else 124


ORDER = ('cleanup', 'checks', 'regression', 'contract', 'review', 'security',
         'ponytail', 'design', 'summary', 'provenance')


def validate_config(config):
    if not isinstance(config, dict) or config.get('version') != 1:
        raise ValueError('Validator configuration requires version 1.')
    validators = config.get('validators')
    if not isinstance(validators, dict):
        raise ValueError('validators must be an object.')
    for name, item in validators.items():
        if name not in ORDER or not isinstance(item, dict):
            raise ValueError(f'Invalid validator: {name}')
        command = item.get('command')
        if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
            raise ValueError(f'{name}: command must be a nonempty array of strings.')
        if item.get('adapter') not in ('json', 'exit-code'):
            raise ValueError(f'{name}: adapter must be json or exit-code.')
        timeout = item.get('timeout')
        if type(timeout) is not int or timeout <= 0:
            raise ValueError(f'{name}: timeout must be a positive integer.')
        if item['adapter'] == 'exit-code' and not (isinstance(item.get('evidence'), str) and item['evidence'].strip()):
            raise ValueError(f'{name}: exit-code adapter requires an evidence description.')
    return config


def normalize_finding(text):
    """Fold a free-text finding into a stable form so repeats hash identically."""
    folded = text.strip().lower()
    folded = re.sub(r'\b[0-9a-f]{8,}\b', '<hex>', folded)
    folded = re.sub(r'([\w./-]+):(\d+)', r'\1:<n>', folded)
    folded = re.sub(r'(?<!\w)\d+(?!\w)', '<n>', folded)
    folded = re.sub(r'\s+', ' ', folded).strip()
    return folded[:160]


def finding_signature(text):
    return hashlib.sha256(normalize_finding(text).encode()).hexdigest()[:12]


def usage_from_verdict(verdict):
    """Accept reported usage without guessing tokens or consulting price tables."""
    usage = verdict.get('usage', {}) if isinstance(verdict, dict) else {}
    result = {}
    if isinstance(usage, dict):
        for key in ('input_tokens', 'output_tokens', 'cost_usd'):
            value = usage.get(key)
            expected = type(value) is int if key.endswith('tokens') else type(value) in (int, float)
            if expected and math.isfinite(value) and value >= 0:
                result[key] = value
    return result


_PATH_TOKEN = re.compile(r'\b([\w-]+(?:/[\w.-]+)+)\b')


def _shared_scope_hint(texts):
    """A directory prefix shared by >=2 findings' path-looking tokens, or None."""
    prefixes = {}
    for text in texts:
        match = _PATH_TOKEN.search(text)
        if not match:
            continue
        parts = match.group(1).split('/')
        if len(parts) < 2:
            continue
        prefix = '/'.join(parts[:-1])
        prefixes[prefix] = prefixes.get(prefix, 0) + 1
    if not prefixes:
        return None
    prefix, count = max(prefixes.items(), key=lambda kv: (kv[1], kv[0]))
    return f'{prefix}/**' if count >= 2 else None


def detect_patterns(rows, min_occurrences=2):
    """A pattern is a (gate, finding hash) pair observed across >=2 distinct tasks.

    This counts a verifiable fact about the recorded log; the finding text itself
    remains an unverified model claim and stays labeled as an example, not a diagnosis.
    """
    gates = [row for row in rows if row.get('event') == 'gate']
    attempts_by_task_gate = {}
    for row in gates:
        attempts_by_task_gate.setdefault((row.get('task_key'), row.get('gate')), []).append(row)
    for entries in attempts_by_task_gate.values():
        entries.sort(key=lambda row: row.get('attempt') or 0)

    occurrences = {}
    for row in gates:
        for finding in row.get('findings') or []:
            if not isinstance(finding, dict):
                continue
            fhash, text = finding.get('hash'), finding.get('text')
            if isinstance(fhash, str) and fhash and isinstance(text, str) and text:
                occurrences.setdefault((row.get('gate'), fhash), []).append((row, text))

    patterns = []
    for (gate, fhash), items in occurrences.items():
        distinct_tasks = {row.get('task_key') for row, _ in items}
        if len(items) < min_occurrences or len(distinct_tasks) < 2:
            continue
        by_profile, by_risk, by_task_type = {}, {}, {}
        resolved_next_attempt = 0
        for row, _ in items:
            for bucket, key in ((by_profile, 'profile'), (by_risk, 'risk'), (by_task_type, 'task_type')):
                value = row.get(key)
                if value: bucket[value] = bucket.get(value, 0) + 1
            entries = attempts_by_task_gate.get((row.get('task_key'), gate), [])
            index = next((i for i, entry in enumerate(entries) if entry is row), None)
            if index is not None and index + 1 < len(entries) and entries[index + 1].get('passed') is True:
                resolved_next_attempt += 1
        timestamps = [row.get('ts') for row, _ in items if isinstance(row.get('ts'), (int, float))]
        patterns.append({
            'id': f'pat_{fhash}', 'gate': gate, 'hash': fhash, 'example': items[0][1],
            'occurrences': len(items), 'distinct_tasks': len(distinct_tasks),
            'first_seen': min(timestamps) if timestamps else None,
            'last_seen': max(timestamps) if timestamps else None,
            'by_profile': by_profile, 'by_risk': by_risk, 'by_task_type': by_task_type,
            'scope_hint': _shared_scope_hint(text for _, text in items),
            'resolved_next_attempt': resolved_next_attempt,
        })
    patterns.sort(key=lambda pattern: (-pattern['occurrences'], pattern['gate'], pattern['hash']))
    return patterns


def outcome_stats(rows, *, profile=None, risk=None, task_type=None, min_n=5):
    """Historical gate outcome frequencies for a stratum, never a single blended score.

    Every returned row carries its own sample size `n`. Below `min_n` a row is marked
    `sufficient: False` and carries no rates at all, rather than a rate computed on too
    few observations that would look more confident than it is. No confidence intervals
    or significance tests: with realistic task volumes those would be theatre.
    """
    gates = [row for row in rows if row.get('event') == 'gate']
    if profile is not None: gates = [row for row in gates if row.get('profile') == profile]
    if risk is not None: gates = [row for row in gates if row.get('risk') == risk]
    if task_type is not None: gates = [row for row in gates if row.get('task_type') == task_type]

    by_gate = {}
    for row in gates:
        by_gate.setdefault(row.get('gate'), []).append(row)

    stats = []
    for gate, entries in sorted(by_gate.items()):
        n = len(entries)
        result = {'gate': gate, 'n': n, 'sufficient': n >= min_n}
        if result['sufficient']:
            first_attempts = [e for e in entries if e.get('attempt') == 1]
            latest_attempt_per_task = {}
            for e in entries:
                key = e.get('task_key')
                latest_attempt_per_task[key] = max(latest_attempt_per_task.get(key, 0), e.get('attempt') or 1)
            usage_totals = [e['usage'].get('input_tokens', 0) + e['usage'].get('output_tokens', 0)
                             for e in entries if isinstance(e.get('usage'), dict) and e['usage']]
            result['pass_rate'] = round(sum(1 for e in entries if e.get('passed') is True) / n, 3)
            result['first_attempt_pass_rate'] = (
                round(sum(1 for e in first_attempts if e.get('passed') is True) / len(first_attempts), 3)
                if first_attempts else None)
            result['median_attempts'] = (statistics.median(latest_attempt_per_task.values())
                                          if latest_attempt_per_task else None)
            result['median_usage_tokens'] = statistics.median(usage_totals) if usage_totals else None
        stats.append(result)
    return stats


def variant_stats(rows, slot):
    """Per-variant outcome stats for one prompt slot, keyed by (variant, sha).

    Grouping by sha too means a mid-experiment text change (a stack upgrade that
    edited a variant file) never silently pools two different prompts under one id.
    """
    gates = [row for row in rows if row.get('event') == 'gate'
             and isinstance(row.get('prompt_variants'), dict) and slot in row['prompt_variants']]
    by_key = {}
    for row in gates:
        info = row['prompt_variants'][slot]
        by_key.setdefault((info.get('variant'), info.get('sha')), []).append(row)

    stats = []
    for (variant, sha), entries in sorted(by_key.items()):
        n = len(entries)
        first_attempts = [e for e in entries if e.get('attempt') == 1]
        usage_totals = [e['usage'].get('input_tokens', 0) + e['usage'].get('output_tokens', 0)
                         for e in entries if isinstance(e.get('usage'), dict) and e['usage']]
        by_task_type, by_risk = {}, {}
        for e in entries:
            for bucket, key in ((by_task_type, 'task_type'), (by_risk, 'risk')):
                value = e.get(key)
                if value: bucket[value] = bucket.get(value, 0) + 1
        stats.append({
            'variant': variant, 'sha': sha, 'n': n,
            'pass_rate': round(sum(1 for e in entries if e.get('passed') is True) / n, 3),
            'first_attempt_pass_rate': (
                round(sum(1 for e in first_attempts if e.get('passed') is True) / len(first_attempts), 3)
                if first_attempts else None),
            'median_usage_tokens': statistics.median(usage_totals) if usage_totals else None,
            'by_task_type': by_task_type, 'by_risk': by_risk,
        })
    return stats


def summarize(rows):
    gates = [row for row in rows if row.get('event') == 'gate']
    counts = {}
    for row in gates:
        key = (row.get('task_key'), row.get('gate'))
        counts[key] = counts.get(key, 0) + 1
    usage = {}
    for key in ('input_tokens', 'output_tokens', 'cost_usd'):
        values = [row['usage'][key] for row in gates if key in row.get('usage', {})]
        usage[key] = {'reported_total': sum(values) if values else None, 'reported_attempts': len(values)}
    pipelines = [row for row in rows if row.get('event') == 'pipeline']
    pipeline_usage = {}
    for key in ('input_tokens', 'output_tokens'):
        values = [row['usage'][key] for row in pipelines if isinstance(row.get('usage'), dict) and key in row['usage']]
        pipeline_usage[key] = {'reported_total': sum(values) if values else None, 'reported_runs': len(values)}
    budget_exceeded = sum(
        row.get('status') != 'PR_READY' and isinstance(row.get('usage'), dict) and row.get('usage_budget')
        and (row['usage'].get('input_tokens', 0) + row['usage'].get('output_tokens', 0)) >= row['usage_budget']
        for row in pipelines
    )
    return {
        'events': len(rows), 'gate_attempts': len(gates),
        'gate_passes': sum(row.get('passed') is True for row in gates),
        'gate_failures': sum(row.get('passed') is False for row in gates),
        'repeated_gate_attempts': sum(max(0, count - 1) for count in counts.values()),
        'gate_duration_seconds': round(sum(row.get('duration_seconds', 0) for row in gates), 3),
        'pipeline_runs': len(pipelines),
        'pipeline_successes': sum(row.get('status') == 'PR_READY' for row in pipelines),
        'pipeline_budget_exceeded': budget_exceeded,
        'usage': usage,
        'pipeline_usage': pipeline_usage,
    }
