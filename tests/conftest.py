"""Suite-wide isolation from the developer's own Codex configuration.

providers.review_settings() reads $CODEX_HOME/config.toml, and its result feeds the
evidence fingerprint and the reviewer's effort; without this the suite would behave
differently on every machine. Tests that need a config point CODEX_HOME elsewhere.
"""
import os
import tempfile

os.environ['CODEX_HOME'] = tempfile.mkdtemp(prefix='codex-home-')
