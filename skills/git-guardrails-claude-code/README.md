# git-guardrails-claude-code

Category: **safety**
Estimated context cost: **low**

Stages: ask scope → copy hook script → register hook in settings → confirm customization → verify block

Curated for AI Agent Stack from Matt Pocock's `git-guardrails-claude-code` skill. Installs a PreToolUse hook (bundled `block-dangerous-git.sh`) that blocks `git push`, `reset --hard`, `clean -f`/`-fd`, `branch -D`, `checkout .` and `restore .` before they execute — a mechanical backstop for this stack's existing git-safety policy.
