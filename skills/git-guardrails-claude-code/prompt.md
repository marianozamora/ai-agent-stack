Install a PreToolUse hook that blocks destructive git commands — push (including `--force`), `reset --hard`, `clean -f`/`-fd`, `branch -D`, `checkout .` and `restore .` — before they can run. Ask the user upfront whether the guardrail applies to this project only (`.claude/settings.json`) or to every project (`~/.claude/settings.json`), and place the hook script accordingly rather than assuming scope.

Copy the bundled `block-dangerous-git.sh` to the target hooks directory, make it executable, and merge — never overwrite — a `PreToolUse`/`Bash` entry pointing at it into the target `settings.json`. Ask whether any pattern should be added to or dropped from the blocked list before finishing, and edit the script in place if so.

Verify the hook actually fires — pipe a synthetic dangerous command through it and confirm it exits 2 with a BLOCKED message on stderr — before telling the user it's installed.
