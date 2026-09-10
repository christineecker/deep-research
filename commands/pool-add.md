Register papers into the pool/registry. No run, no report, no output target.

Do not read SKILL.md's staged pipeline narrative — call the scripts below directly.

**Boundary**: this command registers bibliographic records ONLY (title/authors/journal/
identifiers). No reading, no summary, no appraisal — cheap and fast, one eutils call per
search plus one efetch per paper. For content (reading + summarizing, optionally
appraising) use `/deep-research:summarize-set` instead. `--select-only` on that command
is the middle ground: it picks the set without paying for summaries.

Parse `$ARGUMENTS` for:
- `--repo <path>` — required. If omitted: use cwd if it contains
  `data/papers/registry.jsonl`, else error and point at `/deep-research:init`.
- `--query "<text>"` — PubMed query, OR `--pmid <id>[,<id>...]` for explicit IDs. One of
  the two is required.
- `--n <count>` — default 5. Only used with `--query`.
- `--sort pub_date|relevance` — default `pub_date`.
- `--human-only` — if set, add `--filters-json '{"species":["human"]}'` to the esearch
  call. The value MUST be a JSON list, never a bare string — `_mapped_group` iterates the
  value, and a bare string iterates its characters and raises `bad_filter`.
- `--years <from>-<to>` — optional, maps into the same `--filters-json` object under the
  `years` key. Full supported key set: `years, authors, journals, article_types,
  languages, free_full_text, species, ages` — anything else is a hard error from the
  script, not this command.

Steps:
1. If `--query` given, print then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/eutils.py" esearch --query "<text>" [--filters-json '{"species":["human"],"years":[<from>,<to>]}'] --sort <sort> --retmax <n>
   ```
   Collect PMIDs from the result.
2. For each PMID (from step 1, or from `--pmid`), print then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/registry.py" add --repo <path> --pmid <pmid>
   ```
3. Regenerate the pool projection:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/registry.py" pool --repo <path>
   ```
4. Show what landed:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/registry.py" list --repo <path> --limit <n>
   ```
5. Report: N papers added, path to `pool.jsonl`. Print the script output verbatim —
   don't re-narrate it unless asked to interpret.
