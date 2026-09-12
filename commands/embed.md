Build or query the semantic-similarity index over the registry. No run needed.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

**Optional dependency**: `index` requires the third-party package `sentence-transformers`
(`pip install sentence-transformers`). This is a narrow, deliberate, documented exception
to deep-research's blanket "zero pip installs, ever" policy, scoped to this one script only
(`references/acquisition.md` §9, `references/reference-manager.md`) — no other script gains
a dependency because of this. If it's not installed, `index` fails with a one-line message
saying so; run `pip install sentence-transformers` first. `similar` and `--help` never
require it.

Parse `$ARGUMENTS` for a subcommand:

**`index`**:
- `--repo <path>` — required.
- `--model <name>` — optional, default `all-MiniLM-L6-v2`.
- `--limit N` — optional, only (re)embed the first N missing/stale records.
- `--force` — re-embed even records that already have a cached vector.

Call:
```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/embeddings.py" index --repo <path> [--model <name>] [--limit N] [--force]
```

**`similar`**:
- `--repo <path>` — required.
- `--evidence-id <id>` — required.
- `--k N` — optional, default 5.

Call:
```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/embeddings.py" similar --repo <path> --evidence-id <id> [--k N]
```

Print the script's own output verbatim. Run `index` at least once before `similar` or before
`/deep-research:search --similar-to`.
