Manuscript project lifecycle. Appraisals are project-scoped, so this matters as soon as
the same paper is appraised for two different questions.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

Parse `$ARGUMENTS` for a subcommand:

**`create <slug>`**:
- `<slug>` — required, positional.
- `--repo <path>` — required.
- `--title <text>` — optional (default: the slug).
- `--force` — reuse an existing project directory instead of erroring.

Call:
```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/research.py" project create <slug> --repo <path> [--title "<text>"] [--force]
```

**`list`**:
- `--repo <path>` — required.

Call:
```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/research.py" project list --repo <path>
```

Print the script's own output verbatim. A project slug created here is what
`/deep-research:summarize`, `/deep-research:summarize-set`, and `/deep-research:bib-export
--select appraised` expect in their `--project` flag.
