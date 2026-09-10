Show stage progress + record table for an existing run. Read-only.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

Parse `$ARGUMENTS` for:
- `<run-dir>` — required, POSITIONAL (not `--run-dir`).
- `--table` — show the full corpus table. Without it you get the summary only.
- `--missing` — show only records without full text.
- `--limit <n>` — max rows in table, default 100.
- `--no-summary` — skip the summary, show only the table.

Steps:
1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/status.py" <run-dir> [--table] [--missing] [--limit <n>] [--no-summary]
   ```
2. Print the script's own output verbatim — don't re-narrate it unless asked to
   interpret.

Pass `--table` when the user asks "which papers am I missing" or wants the per-record
view — `--missing` alone narrows that table to gaps.
