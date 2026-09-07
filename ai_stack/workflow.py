"""Reusable validator configuration and task metric aggregation (stdlib only)."""
import math
import os
import signal
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
