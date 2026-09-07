"""Reusable validator configuration and task metric aggregation (stdlib only)."""
import datetime
import hashlib
import math
import os
import re
import signal
import statistics
import subprocess
import time


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


def _usage_totals(entries):
    """Reported input+output tokens per entry that carried any usage at all."""
    return [e['usage'].get('input_tokens', 0) + e['usage'].get('output_tokens', 0)
            for e in entries if isinstance(e.get('usage'), dict) and e['usage']]


def _pass_rate_stats(entries):
    """Overall and first-attempt pass rate for a group of gate events. None if not applicable."""
    n = len(entries)
    first_attempts = [e for e in entries if e.get('attempt') == 1]
    return {
        'pass_rate': round(sum(1 for e in entries if e.get('passed') is True) / n, 3) if n else None,
        'first_attempt_pass_rate': (
            round(sum(1 for e in first_attempts if e.get('passed') is True) / len(first_attempts), 3)
            if first_attempts else None),
    }


def _bucket_counts(entries, keys):
    """Occurrence counts per distinct value, one bucket per requested row key."""
    buckets = {key: {} for key in keys}
    for e in entries:
        for key, bucket in buckets.items():
            value = e.get(key)
            if value: bucket[value] = bucket.get(value, 0) + 1
    return buckets


def detect_patterns(rows, min_occurrences=2):
    """A pattern is a (gate, finding hash) pair observed across >=2 distinct tasks.

    This counts a verifiable fact about the recorded log; the finding text itself
    remains an unverified model claim and stays labeled as an example, not a diagnosis.
    """
    gates = [row for row in rows if row.get('event') == 'gate']
    attempts_by_task_gate = {}
    for row in gates:
        attempts_by_task_gate.setdefault((row.get('task_key'), row.get('gate')), []).append(row)
    row_index = {}
    for entries in attempts_by_task_gate.values():
        entries.sort(key=lambda row: row.get('attempt') or 0)
        for i, entry in enumerate(entries):
            row_index[id(entry)] = i

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
        buckets = _bucket_counts([row for row, _ in items], ('profile', 'risk', 'task_type'))
        resolved_next_attempt = 0
        for row, _ in items:
            entries = attempts_by_task_gate.get((row.get('task_key'), gate), [])
            index = row_index.get(id(row))
            if index is not None and index + 1 < len(entries) and entries[index + 1].get('passed') is True:
                resolved_next_attempt += 1
        timestamps = [row.get('ts') for row, _ in items if isinstance(row.get('ts'), (int, float))]
        patterns.append({
            'id': f'pat_{fhash}', 'gate': gate, 'hash': fhash, 'example': items[0][1],
            'occurrences': len(items), 'distinct_tasks': len(distinct_tasks),
            'first_seen': min(timestamps) if timestamps else None,
            'last_seen': max(timestamps) if timestamps else None,
            'by_profile': buckets['profile'], 'by_risk': buckets['risk'], 'by_task_type': buckets['task_type'],
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
            latest_attempt_per_task = {}
            for e in entries:
                key = e.get('task_key')
                latest_attempt_per_task[key] = max(latest_attempt_per_task.get(key, 0), e.get('attempt') or 1)
            result.update(_pass_rate_stats(entries))
            result['median_attempts'] = (statistics.median(latest_attempt_per_task.values())
                                          if latest_attempt_per_task else None)
            usage_totals = _usage_totals(entries)
            result['median_usage_tokens'] = statistics.median(usage_totals) if usage_totals else None
        stats.append(result)
    return stats


def variant_stats(rows, slot, since=None):
    """Per-variant outcome stats for one prompt slot, keyed by (variant, sha).

    Grouping by sha too means a mid-experiment text change (a stack upgrade that
    edited a variant file) never silently pools two different prompts under one id.
    `since` (a timestamp) excludes samples recorded before it, so restarting an
    experiment on the same slot never lets an earlier, unrelated run's history
    silently satisfy this run's sample-size requirement. `by_stack_version` surfaces
    a stack upgrade mid-experiment as a visible confounder, the same class of thing
    `(variant, sha)` grouping already exists to catch for the variant text itself.
    """
    gates = [row for row in rows if row.get('event') == 'gate'
             and isinstance(row.get('prompt_variants'), dict) and slot in row['prompt_variants']
             and (since is None or row.get('ts', 0) >= since)]
    by_key = {}
    for row in gates:
        info = row['prompt_variants'][slot]
        by_key.setdefault((info.get('variant'), info.get('sha')), []).append(row)

    stats = []
    for (variant, sha), entries in sorted(by_key.items()):
        usage_totals = _usage_totals(entries)
        buckets = _bucket_counts(entries, ('task_type', 'risk', 'stack_version'))
        stats.append({
            'variant': variant, 'sha': sha, 'n': len(entries),
            **_pass_rate_stats(entries),
            'median_usage_tokens': statistics.median(usage_totals) if usage_totals else None,
            'by_task_type': buckets['task_type'], 'by_risk': buckets['risk'],
            'by_stack_version': buckets['stack_version'],
        })
    return stats


_WINDOW = re.compile(r'^(\d+)([dw])$')


def parse_window(spec):
    """'30d'/'12w' -> epoch cutoff N days/weeks ago; 'YYYY-MM-DD' -> that UTC midnight."""
    match = _WINDOW.match(spec)
    if match:
        n, unit = int(match.group(1)), match.group(2)
        return time.time() - n * (86400 if unit == 'd' else 604800)
    try:
        dt = datetime.datetime.strptime(spec, '%Y-%m-%d').replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        raise ValueError(f"Invalid window: {spec!r} (expected '30d', '12w', or 'YYYY-MM-DD')")
    return dt.timestamp()


def bucket_ts(ts, granularity):
    """A UTC bucket label for a timestamp: 'YYYY-MM-DD' (day) or ISO 'YYYY-Www' (week)."""
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    if granularity == 'day':
        return dt.strftime('%Y-%m-%d')
    if granularity == 'week':
        iso_year, iso_week, _ = dt.isocalendar()
        return f'{iso_year}-W{iso_week:02d}'
    raise ValueError(f'Unknown granularity: {granularity}')


def usage_report(rows, *, group_by, since=None, until=None, top=None):
    """Aggregated gate/pipeline usage grouped by a time bucket or a row dimension.

    Unreported usage stays None and is counted separately via reported/unreported
    attempts, never zero-filled — same honesty rule summarize() already applies.
    `group_by` is 'day', 'week', or a row field ('gate', 'profile', 'task_type', 'task').
    """
    def in_window(row):
        ts = row.get('ts', 0)
        return (since is None or ts >= since) and (until is None or ts < until)

    def key_of(row):
        if group_by in ('day', 'week'): return bucket_ts(row.get('ts', 0), group_by)
        if group_by == 'task': return row.get('task_key')
        return row.get(group_by)

    buckets = {}
    for row in rows:
        if row.get('event') not in ('gate', 'pipeline') or not in_window(row): continue
        bucket = buckets.setdefault(key_of(row), {'gates': [], 'pipelines': [], 'task_id': row.get('task_id')})
        bucket['gates' if row['event'] == 'gate' else 'pipelines'].append(row)

    report = []
    for key, bucket in buckets.items():
        entries = bucket['gates']
        usage_values = {field: [] for field in ('input_tokens', 'output_tokens', 'cost_usd')}
        reported = 0
        for e in entries:
            usage = e.get('usage')
            if isinstance(usage, dict) and usage:
                reported += 1
                for field, values in usage_values.items():
                    if field in usage: values.append(usage[field])
        row = {
            'key': key,
            'gate_attempts': len(entries),
            'gate_passes': sum(1 for e in entries if e.get('passed') is True),
            'gate_failures': sum(1 for e in entries if e.get('passed') is False),
            'pipeline_runs': len(bucket['pipelines']),
            'pipeline_successes': sum(1 for p in bucket['pipelines'] if p.get('status') == 'PR_READY'),
            'reported_attempts': reported, 'unreported_attempts': len(entries) - reported,
            'input_tokens': sum(usage_values['input_tokens']) if usage_values['input_tokens'] else None,
            'output_tokens': sum(usage_values['output_tokens']) if usage_values['output_tokens'] else None,
            'cost_usd': sum(usage_values['cost_usd']) if usage_values['cost_usd'] else None,
            'cost_reported_attempts': len(usage_values['cost_usd']),
        }
        row['total_tokens'] = (None if row['input_tokens'] is None and row['output_tokens'] is None
                                else (row['input_tokens'] or 0) + (row['output_tokens'] or 0))
        if group_by == 'task': row['task_id'] = bucket['task_id']
        report.append(row)

    report.sort(key=lambda r: -(r['total_tokens'] or 0) if top else str(r['key']))
    return report[:top] if top else report


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
