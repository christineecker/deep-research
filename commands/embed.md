Build or query the semantic-similarity index over the registry. No run needed.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

**Optional dependency**: `index` requires the third-party package `sentence-transformers`
(`pip install sentence-transformers`). This is a narrow, deliberate, documented exception
to deep-research's blanket "zero pip installs, ever" policy, scoped to this one script only
(`references/acquisition.md` §9, `references/reference-manager.md`) — no other script gains
a dependency because of this. If it's not installed, `index` fails with a one-line message
saying so; run `pip install sentence-transformers` first. `query` needs it too (it has to
embed the question); `similar` and `--help` never require it.

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

**`query`** — rank papers against a free-text question rather than against another paper:
- `--repo <path>` — required.
- `--text "<question>"` — required.
- `--k N` — optional, default 10.
- `--model <name>` — optional. Only needed when `embeddings.jsonl` holds vectors from more
  than one model: rankings are never computed across models, so the command asks which one
  to use instead of mixing them.

Call:
```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/embeddings.py" query --repo <path> --text "<question>" [--k N] [--model <name>]
```

Print the script's own output verbatim. Run `index` at least once before `similar`, `query`,
or `/deep-research:search --similar-to`.
