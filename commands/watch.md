Read-only snapshot of a run's progress. Never writes to the run, never takes the
taskboard lock, never uses the network.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

**Non-interactive by default.** The underlying `watch.py` is a curses TUI when run bare,
which does not work through a headless agent harness. This command always passes
`--once` (or `--json`) instead — a single snapshot, no TTY, no live redraw. If the user
explicitly wants the live interactive TUI, tell them to run `watch.py` themselves in a
real terminal; don't attempt to drive curses from here.

Parse `$ARGUMENTS` for:
- `--run-dir <dir>` — one of this or `--follow-latest` required.
- `--follow-latest <wiki-root>` — attach to the newest run under
  `<wiki-root>/outputs/deep-research/`.
- `--json` — machine-readable snapshot instead of the plain-text one.
- `--width <n>` — line width for the plain-text snapshot (default 100).
- `--no-color` — disable colour.

Steps:
1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/watch.py" (--run-dir <dir>|--follow-latest <wiki>) --once [--json] [--width <n>] [--no-color]
   ```
2. Print the script's own output verbatim.
3. For "which papers am I missing" detail, `/deep-research:status <run-dir> --missing`
   gives the per-record table this snapshot summarizes.
