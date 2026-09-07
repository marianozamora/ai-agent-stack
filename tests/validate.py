"""Dependency-free source and data validation, shared by local checks and CI."""
import ast
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
for directory in ('ai_stack', 'tests'):
    for path in (root / directory).glob('*.py'):
        ast.parse(path.read_text(), filename=str(path))
for path in (root / 'skills').rglob('*.json'):
    json.loads(path.read_text())
for path in [root / 'install.sh', *(root / 'bin').iterdir(), *(root / 'tests').glob('*.sh'), root / 'templates/lib/common.sh']:
    if path.is_file() and path.read_text().startswith('#!/usr/bin/env bash'):
        subprocess.run(['bash', '-n', str(path)], check=True)
version = subprocess.check_output([str(root / 'bin/ai'), '--version'], text=True).strip()
assert version == (root / 'VERSION').read_text().strip()
print('validation: PASS')
