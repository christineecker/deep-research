Export BibTeX from the registry. No run needed.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

Parse `$ARGUMENTS` for:
- `--repo <path>` — required.
- `--out <file.bib>` — required.
- `--select all|extracted|appraised` — default `all`.
- `--project <slug>` — required when `--select appraised` (the script enforces this:
  appraised records are project-scoped, per `references/pool-architecture.md`).

Steps:
1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/registry.py" bib --repo <path> --out <file> --select <select> [--project <slug>]
   ```
2. Report the output path and how many entries were written.
