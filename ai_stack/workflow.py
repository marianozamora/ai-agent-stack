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
from collections.abc import Iterable
from typing import Any


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
            if expected and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                result[key] = value
    return result


_PATH_TOKEN = re.compile(r'\b([\w-]+(?:/[\w.-]+)+)\b')


def _shared_scope_hint(texts: Iterable[str]):
    """A directory prefix shared by >=2 findings' path-looking tokens, or None."""
    prefixes: dict[str, int] = {}
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
    buckets: dict[str, dict[Any, int]] = {key: {} for key in keys}
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
    attempts_by_task_gate: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for row in gates:
        attempts_by_task_gate.setdefault((row.get('task_key'), row.get('gate')), []).append(row)
    row_index = {}
    for entries in attempts_by_task_gate.values():
        entries.sort(key=lambda row: row.get('attempt') or 0)
        for i, entry in enumerate(entries):
            row_index[id(entry)] = i

    occurrences: dict[tuple[Any, str], list[tuple[dict[str, Any], str]]] = {}
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
        timestamps: list[int | float] = [timestamp for row, _ in items
            if isinstance((timestamp := row.get('ts')), (int, float))]
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

    by_gate: dict[Any, list[dict[str, Any]]] = {}
    for row in gates:
        by_gate.setdefault(row.get('gate'), []).append(row)

    stats = []
    for gate, entries in sorted(by_gate.items()):
        n = len(entries)
        result: dict[str, Any] = {'gate': gate, 'n': n, 'sufficient': n >= min_n}
        if result['sufficient']:
            latest_attempt_per_task: dict[Any, int] = {}
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
    by_key: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
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


_FIGMA_URL = re.compile(r'https?://\S*figma\.com/\S+')
_BLOCKER_MENTION = re.compile(r'(?im)^\s*(?:[-*]\s*)?(?:blocked by|blocker|depends on|dependency|waiting on)\s*[:\-]?\s*(.+)$')
_ACCEPTANCE_HEADING = re.compile(r'(?im)^#{0,6}\s*acceptance\s*criteria\s*:?\s*$')
_CHECKLIST_ITEM = re.compile(r'(?m)^\s*[-*]\s*\[[ xX]?\]\s*(.+)$')
_PLAIN_BULLET = re.compile(r'(?m)^\s*[-*]\s+(.+)$')


def analyze_ticket_text(text):
    """Deterministic, regex-based completeness read of pasted ticket content.

    No model call, no network, no external fetch: this only reports what a pattern
    matches, never an interpretation of whether the ticket is actually sufficient to
    start work. Blockers/dependencies are advisory-only for the same reason — there is
    no live source to verify whether a mentioned blocker is still actually open.
    """
    figma = _FIGMA_URL.search(text)
    blockers = [m.group(1).strip() for m in _BLOCKER_MENTION.finditer(text)]
    acceptance_items = [m.group(1).strip() for m in _CHECKLIST_ITEM.finditer(text)]
    heading = _ACCEPTANCE_HEADING.search(text)
    if heading:
        rest = text[heading.end():]
        next_heading = re.search(r'(?m)^#{1,6}\s', rest)
        block = rest[:next_heading.start()] if next_heading else rest
        for m in _PLAIN_BULLET.finditer(block):
            item = m.group(1).strip()
            if item not in acceptance_items: acceptance_items.append(item)
    return {
        'figma_url': figma.group(0) if figma else None,
        'acceptance_items': acceptance_items,
        'blockers_mentioned': blockers,
        'has_acceptance': bool(acceptance_items),
        'length': len(text),
    }


def parse_window(spec):
    """'30d'/'12w' -> epoch cutoff N days/weeks ago; 'YYYY-MM-DD' -> that UTC midnight."""
    match = _WINDOW.match(spec)
    if match:
        n, unit = int(match.group(1)), match.group(2)
        return time.time() - n * (86400 if unit == 'd' else 604800)
    try:
        dt = datetime.datetime.strptime(spec, '%Y-%m-%d').replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        raise ValueError(f"Invalid window: {spec!r} (expected '30d', '12w', or 'YYYY-MM-DD')") from None
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

    buckets: dict[Any, dict[str, Any]] = {}
    for row in rows:
        if row.get('event') not in ('gate', 'pipeline') or not in_window(row): continue
        bucket = buckets.setdefault(key_of(row), {'gates': [], 'pipelines': [], 'task_id': row.get('task_id')})
        bucket['gates' if row['event'] == 'gate' else 'pipelines'].append(row)

    report: list[dict[str, Any]] = []
    for key, bucket in buckets.items():
        entries = bucket['gates']
        usage_values: dict[str, list[int | float]] = {field: [] for field in ('input_tokens', 'output_tokens', 'cost_usd')}
        reported = 0
        for e in entries:
            usage = e.get('usage')
            if isinstance(usage, dict) and usage:
                reported += 1
                for field, values in usage_values.items():
                    if field in usage: values.append(usage[field])
        report_row: dict[str, Any] = {
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
        report_row['total_tokens'] = (None if report_row['input_tokens'] is None and report_row['output_tokens'] is None
                                else (report_row['input_tokens'] or 0) + (report_row['output_tokens'] or 0))
        if group_by == 'task': report_row['task_id'] = bucket['task_id']
        report.append(report_row)

    report.sort(key=lambda r: -(r['total_tokens'] or 0) if top else str(r['key']))
    return report[:top] if top else report


def summarize(rows):
    gates = [row for row in rows if row.get('event') == 'gate']
    counts: dict[tuple[Any, Any], int] = {}
    for row in gates:
        key = (row.get('task_key'), row.get('gate'))
        counts[key] = counts.get(key, 0) + 1
    usage: dict[str, dict[str, Any]] = {}
    for usage_key in ('input_tokens', 'output_tokens', 'cost_usd'):
        values = [row['usage'][usage_key] for row in gates if usage_key in row.get('usage', {})]
        usage[usage_key] = {'reported_total': sum(values) if values else None, 'reported_attempts': len(values)}
    pipelines = [row for row in rows if row.get('event') == 'pipeline']
    pipeline_usage: dict[str, dict[str, Any]] = {}
    for usage_key in ('input_tokens', 'output_tokens'):
        values = [row['usage'][usage_key] for row in pipelines if isinstance(row.get('usage'), dict) and usage_key in row['usage']]
        pipeline_usage[usage_key] = {'reported_total': sum(values) if values else None, 'reported_runs': len(values)}
    def stopped_on_budget(row):
        if row.get('status') == 'BUDGET_EXCEEDED':
            return True
        # Rows recorded before BUDGET_EXCEEDED existed carry status FAILED, so a
        # historical overrun is still inferred from reported usage against the budget.
        # A run that finished with --allow-overrun is PR_READY and never counts.
        if row.get('status') in ('PR_READY', 'BUDGET_EXCEEDED') or not isinstance(row.get('usage'), dict):
            return False
        budget = row.get('usage_budget')
        spent = row['usage'].get('input_tokens', 0) + row['usage'].get('output_tokens', 0)
        return bool(budget) and spent >= budget

    budget_exceeded = sum(stopped_on_budget(row) for row in pipelines)
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


def _percentile(values, p):
    """Nearest-rank percentile of a sample; None for an empty one.

    Deliberately not `statistics.quantiles()`'s interpolated methods: a real-world
    sample here is small (tens of tasks), and a value that always lands on an actual
    observation is easier to explain in a report than an interpolated one.
    """
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))
    return ordered[index]


FINDINGS_RAISED_CAVEAT = (
    "Distinct findings a task's own failed gate attempts raised before the gate passed -- "
    "a volume signal, not a measure of what a human ignored. This stack cannot tell an "
    "override from a genuine fix without a human label (see `ai metrics label`).")


def campaign_report(rows, *, since=None, usage_budgets=None):
    """Aggregate a real-usage validation campaign from recorded events plus human labels.

    Reads `task_start`/`plan` (task start), `gate`/`pipeline` (evidence and outcomes),
    `task_close` (final readiness) and `gate_label` (`ai metrics label`) events. Computes,
    per task and then stratified by task_type and by profile: time to first PR_READY,
    retries per gate, tokens, and — where labeled — each gate's false-positive rate.
    Never infers a false positive or an ignored finding from outcomes alone; both require
    an explicit human label or are reported as an explicitly caveated volume count.

    `usage_budgets` is an optional {profile: usage_tokens} map (from `core.context_caps`,
    which this stdlib-only module cannot import itself) used only to flag a profile whose
    median consumption is eating most of its budget.
    """
    if since is not None:
        rows = [row for row in rows if row.get('ts', 0) >= since]

    by_task: dict[Any, dict[str, list]] = {}
    for row in rows:
        key = row.get('task_key')
        if key is None:
            continue
        bucket = by_task.setdefault(key, {'starts': [], 'plans': [], 'gates': [], 'pipelines': [], 'closes': []})
        event = row.get('event')
        if event == 'task_start':
            bucket['starts'].append(row)
        elif event == 'plan':
            bucket['plans'].append(row)
        elif event == 'gate':
            bucket['gates'].append(row)
        elif event == 'pipeline':
            bucket['pipelines'].append(row)
        elif event == 'task_close':
            bucket['closes'].append(row)

    labels: dict[Any, list[dict]] = {}
    for row in rows:
        if row.get('event') == 'gate_label':
            labels.setdefault(row.get('gate'), []).append(row)

    tasks = []
    for key, bucket in by_task.items():
        plans, starts, closes = bucket['plans'], bucket['starts'], bucket['closes']
        task_type = plans[-1].get('task_type') if plans else None
        profile = plans[-1].get('profile') if plans else None
        task_id = (starts[0].get('task_id') if starts else plans[0].get('task_id') if plans else None)

        start_candidates = [row['ts'] for row in starts + plans if isinstance(row.get('ts'), (int, float))]
        started_at = min(start_candidates) if start_candidates else None
        ready_candidates = [row['ts'] for row in bucket['pipelines']
                            if row.get('status') == 'PR_READY' and isinstance(row.get('ts'), (int, float))]
        ready_at = min(ready_candidates) if ready_candidates else None
        time_to_ready = (ready_at - started_at if started_at is not None and ready_at is not None
                         and ready_at >= started_at else None)
        if closes:
            final_status = closes[-1].get('readiness')
        elif bucket['pipelines']:
            final_status = bucket['pipelines'][-1].get('status')
        else:
            final_status = None

        retries_by_gate: dict[str, int] = {}
        duration_by_gate: dict[str, float] = {}
        findings_seen: set[str] = set()
        usage_values: dict[str, list[int]] = {'input_tokens': [], 'output_tokens': []}
        for gate_row in bucket['gates']:
            gate_name = gate_row.get('gate')
            if gate_name:
                attempt = gate_row.get('attempt') or 1
                retries_by_gate[gate_name] = max(retries_by_gate.get(gate_name, 1), attempt)
                duration_by_gate[gate_name] = duration_by_gate.get(gate_name, 0) + (gate_row.get('duration_seconds') or 0)
            for finding in gate_row.get('findings') or []:
                if isinstance(finding, dict) and isinstance(finding.get('hash'), str):
                    findings_seen.add(finding['hash'])
            usage = gate_row.get('usage')
            if isinstance(usage, dict):
                for field in usage_values:
                    if field in usage:
                        usage_values[field].append(usage[field])

        tasks.append({
            'task_key': key, 'task_id': task_id, 'task_type': task_type, 'profile': profile,
            'started_at': started_at, 'ready_at': ready_at, 'time_to_ready_seconds': time_to_ready,
            'reached_pr_ready': ready_at is not None, 'final_status': final_status,
            'retries_by_gate': retries_by_gate, 'duration_by_gate': duration_by_gate,
            'max_retries': max(retries_by_gate.values()) if retries_by_gate else 1,
            'findings_raised': len(findings_seen),
            'input_tokens': sum(usage_values['input_tokens']) if usage_values['input_tokens'] else None,
            'output_tokens': sum(usage_values['output_tokens']) if usage_values['output_tokens'] else None,
        })

    def _stratify(field):
        groups: dict[Any, list[dict]] = {}
        for task in tasks:
            groups.setdefault(task[field], []).append(task)
        report = {}
        for value, entries in groups.items():
            times = [t['time_to_ready_seconds'] for t in entries if t['time_to_ready_seconds'] is not None]
            totals = [(t['input_tokens'] or 0) + (t['output_tokens'] or 0) for t in entries
                     if t['input_tokens'] is not None or t['output_tokens'] is not None]
            duration_totals: dict[str, float] = {}
            for t in entries:
                for gate, seconds in t['duration_by_gate'].items():
                    duration_totals[gate] = duration_totals.get(gate, 0) + seconds
            dominant_gate = max(duration_totals, key=lambda g: duration_totals[g]) if duration_totals else None
            median_time = round(statistics.median(times), 1) if times else None
            p90_time = round(_percentile(times, 90), 1) if times else None
            report[value] = {
                'n': len(entries),
                'reached_pr_ready': sum(1 for t in entries if t['reached_pr_ready']),
                'median_time_to_ready_seconds': median_time,
                'p90_time_to_ready_seconds': p90_time,
                'dominant_gate_by_duration': dominant_gate,
                'median_retries': round(statistics.median([t['max_retries'] for t in entries]), 2),
                'max_retries': max(t['max_retries'] for t in entries),
                'median_tokens': round(statistics.median(totals)) if totals else None,
                'reporting_tasks_for_tokens': len(totals),
                'median_findings_raised': round(statistics.median([t['findings_raised'] for t in entries]), 1),
            }
        return report

    by_task_type = _stratify('task_type')
    by_profile = _stratify('profile')

    gate_labels: dict[str, dict] = {}
    for gate, entries in labels.items():
        true_positive = sum(1 for e in entries if e.get('label') == 'true_positive')
        false_positive = sum(1 for e in entries if e.get('label') == 'false_positive')
        labeled = true_positive + false_positive
        gate_labels[gate] = {'true_positive': true_positive, 'false_positive': false_positive,
                             'labeled': labeled,
                             'false_positive_rate': round(false_positive / labeled, 3) if labeled else None}

    recommendations = []
    min_labeled = 5  # below this, one relabeled sample would flip the recommendation
    for gate, stats in sorted(gate_labels.items()):
        if stats['labeled'] >= min_labeled and stats['false_positive_rate'] is not None and stats['false_positive_rate'] > 0.20:
            recommendations.append(
                f"{gate}: false-positive rate {stats['false_positive_rate']:.0%} over {stats['labeled']} labeled "
                f"attempts (>20%) -- consider making it advisory (non-blocking) by default.")
    for task_type, stats in sorted(by_task_type.items(), key=lambda kv: str(kv[0])):
        if stats['median_time_to_ready_seconds'] and stats['p90_time_to_ready_seconds']:
            if stats['p90_time_to_ready_seconds'] > 3 * stats['median_time_to_ready_seconds']:
                gate_note = f" ({stats['dominant_gate_by_duration']} dominates gate duration)" if stats['dominant_gate_by_duration'] else ''
                recommendations.append(
                    f"{task_type or 'unclassified'}: p90 time to PR_READY is "
                    f"{stats['p90_time_to_ready_seconds']:.0f}s vs a median of {stats['median_time_to_ready_seconds']:.0f}s "
                    f"(>3x) -- investigate outliers{gate_note}.")
    if usage_budgets:
        for profile, stats in sorted(by_profile.items(), key=lambda kv: str(kv[0])):
            budget = usage_budgets.get(profile)
            if budget and stats['median_tokens'] and stats['median_tokens'] > 0.8 * budget:
                recommendations.append(
                    f"{profile or 'unclassified'}: median usage {stats['median_tokens']} tokens is over 80% "
                    f"of its {budget}-token budget -- recalibrate context_caps for this profile.")

    return {
        'tasks': len(tasks), 'reached_pr_ready': sum(1 for t in tasks if t['reached_pr_ready']),
        'by_task_type': by_task_type, 'by_profile': by_profile, 'gate_labels': gate_labels,
        'findings_raised_caveat': FINDINGS_RAISED_CAVEAT,
        'recommendations': recommendations,
        'tasks_detail': sorted(tasks, key=lambda t: (str(t['task_type']), str(t['task_key']))),
    }
