#!/usr/bin/env python3
"""Validate a staged release before switching the global installation."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid


def install(source, destination, bin_home):
    source, destination, bin_home = map(Path, (source, destination, bin_home))
    destination.parent.mkdir(parents=True, exist_ok=True)
    bin_home.mkdir(parents=True, exist_ok=True)
    releases = destination.parent / 'ai-agent-stack-releases'
    releases.mkdir(exist_ok=True)
    release = Path(tempfile.mkdtemp(prefix='release-', dir=releases))
    staged_link = destination.parent / ('.ai-stack-' + uuid.uuid4().hex)
    command_link = bin_home / ('.ai-' + uuid.uuid4().hex)
    backup = None
    switched = False
    previous = os.readlink(destination) if destination.is_symlink() else None
    try:
        for name in ('ai_stack', 'bin', 'templates', 'skills', 'VERSION', 'LICENSE', 'README.md', 'CHANGELOG.md', 'install.sh'):
            item = source / name
            if item.is_dir():
                shutil.copytree(item, release / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            else:
                shutil.copy2(item, release / name)
        version = (release / 'VERSION').read_text().strip()
        actual = subprocess.check_output([sys.executable, str(release / 'ai_stack/cli.py'), '--version'], text=True).strip()
        if actual != version:
            raise RuntimeError('Staged CLI version does not match VERSION')
        subprocess.run([sys.executable, str(release / 'ai_stack/cli.py'), '--help'], check=True, stdout=subprocess.DEVNULL)
        for executable in ('bin/ai', 'ai_stack/cli.py', 'install.sh'):
            path = release / executable
            path.chmod(path.stat().st_mode | 0o111)
        staged_link.symlink_to(release, target_is_directory=True)
        command_link.symlink_to(destination / 'bin/ai')
        if destination.exists() and not destination.is_symlink():
            backup = releases / ('previous-' + uuid.uuid4().hex)
            destination.rename(backup)
        os.replace(staged_link, destination)
        switched = True
        os.replace(command_link, bin_home / 'ai')
    except BaseException:
        if switched:
            if previous is not None:
                staged_link.symlink_to(previous, target_is_directory=True)
                os.replace(staged_link, destination)
            else:
                destination.unlink()
        if backup is not None:
            backup.rename(destination)
        shutil.rmtree(release)
        raise
    finally:
        staged_link.unlink(missing_ok=True)
        command_link.unlink(missing_ok=True)
    # Older releases are retained so an interrupted process or running CLI keeps its files.
    return release


if __name__ == '__main__':
    install(*sys.argv[1:])
