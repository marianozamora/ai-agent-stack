#!/usr/bin/env python3
"""Ad-hoc benchmark for evidence_fingerprint() -- not installed, not part of CI.

Measures, on the repository it is run from, how much of evidence_fingerprint()'s
cost is git subprocess spawns vs. content hashing, so a change to that function
can be justified with numbers instead of intuition. See docs/architecture.md /
the Fase 1 plan for context: this function runs multiple times per gate.
"""
from __future__ import annotations
import hashlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import core  # noqa: E402
import gates  # noqa: E402


def timed(fn, n=5):
    start = time.time()
    for _ in range(n):
        fn()
    return (time.time() - start) / n * 1000


def main():
    root = core.git_root()
    state = core.repo_state(root)
    plan = {'scope': {'base': 'HEAD'}, 'profile': 'standard'}

    names = [n for n in core.run(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root
    ).split('\0') if n]
    sizes = [os.path.getsize(root / n) for n in names if (root / n).is_file()]
    total_mb = sum(sizes) / 1e6

    def full_fingerprint():
        return gates.evidence_fingerprint(root, state, plan)

    def git_calls_only():
        core.safe_head(root)
        core.run(['git', 'rev-parse', '--verify', plan['scope']['base']], cwd=root)
        core.run(['git', 'ls-files', '--stage'], cwd=root)
        core.run(['git', 'diff', '--binary', 'HEAD'], cwd=root)
        core.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root)

    def hash_content_only():
        digest = hashlib.sha256()
        for name in sorted(set(names)):
            path = root / name
            if path.is_file():
                with path.open('rb') as f:
                    for block in iter(lambda: f.read(1024 * 1024), b''):
                        digest.update(block)
        return digest.hexdigest()

    def stat_only():
        digest = hashlib.sha256()
        for name in sorted(set(names)):
            path = root / name
            try:
                st = path.lstat()
                digest.update(f'{name}{st.st_mode}{st.st_size}{st.st_mtime_ns}'.encode())
            except OSError:
                pass
        return digest.hexdigest()

    print(f'Repository: {root}')
    print(f'Files: {len(names)}  Total size: {total_mb:.2f} MB\n')
    print(f'{"full evidence_fingerprint()":45} {timed(full_fingerprint):8.1f} ms')
    print(f'{"  of which: 5 git subprocess calls":45} {timed(git_calls_only):8.1f} ms')
    print(f'{"  of which: content hashing":45} {timed(hash_content_only):8.1f} ms')
    print(f'{"(for comparison) stat-only scan":45} {timed(stat_only):8.1f} ms')


if __name__ == '__main__':
    main()
