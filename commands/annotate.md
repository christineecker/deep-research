Personal tags/rating/note on a paper, kept separate from the registry and from
project-scoped appraisal. No run needed.

Do not read SKILL.md's staged pipeline narrative — call the scripts below directly.

Parse `$ARGUMENTS` for a subcommand:

**`tag`**:
- `--repo <path>` — required.
- `--evidence-id <id>` — required.
- `--add <tag>` | `--remove <tag>` — one or the other.

Call:
```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/annotations.py" tag --repo <path> --evidence-id <id> (--add <tag>|--remove <tag>)
```

**`rate`**:
- `--repo <path>` — required.
- `--evidence-id <id>` — required.
- `--stars N` (1-5) | `--clear`.

Call:
```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/annotations.py" rate --repo <path> --evidence-id <id> (--stars N|--clear)
```

**`note`**:
- `--repo <path>` — required.
- `--evidence-id <id>` — required.
- `--set "<text>"` | `--clear`.

Call:
```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/annotations.py" note --repo <path> --evidence-id <id> (--set "<text>"|--clear)
```

**`show`**:
- `--repo <path>` — required.
- `--evidence-id <id>` — required.

Call:
```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/annotations.py" show --repo <path> --evidence-id <id>
```

**`list`**:
- `--repo <path>` — required.
- `--tag <tag>` — optional filter.
- `--min-rating N` — optional filter.
- `--limit N` — optional cap.

Call:
```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/annotations.py" list --repo <path> [--tag <tag>] [--min-rating N] [--limit N]
```

Print the script's own output verbatim. An annotation never requires its evidence_id to
already exist in the registry (`references/schema/16-annotation.md`).
