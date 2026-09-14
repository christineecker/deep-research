Saved PubMed searches, re-run to show what is new since last time. No run needed.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

Parse `$ARGUMENTS` for a subcommand:

**`save`** — store a query to re-run later:
- `--repo <path>` — required.
- `--name <name>` — required, unique per repo.
- `--query "<pubmed query>"` — required. Plain PubMed syntax, same as
  `/deep-research:pool-add`'s `--query`.
- `--filters-json '<json>'` — optional `eutils.py` filter object (`years`, `journals`,
  `article_types`, `languages`, `species`, `ages`, `free_full_text`). Validated now, so a
  bad filter fails here rather than on every future rerun.
- `--force` — replace an existing saved search's query, keeping its name and its
  last-run baseline.

```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/alerts.py" save --repo <path> --name <name> --query "<pubmed query>" [--filters-json '<json>'] [--force]
```

**`list`** / **`delete`**:
```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/alerts.py" list --repo <path> [--limit N]
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/alerts.py" delete --repo <path> --name <name>
```

**`run`** — re-run and diff against the registry:
- `--repo <path>` — required.
- `--name <name>` — optional, repeatable. Default: every saved search.
- `--since <YYYY/MM/DD>` — optional entry-date floor. Default: when that search last ran.
- `--retmax N` — optional cap on hits per search (default 200).
- `--register` — register the new hits into the registry as well as reporting them.
- `--dry-run` — do not move the saved search's baseline forward.
- `--email <addr>` — contact email for NCBI (or `$DEEP_RESEARCH_EMAIL`).

```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/alerts.py" run --repo <path> [--name <name>] [--since <YYYY/MM/DD>] [--retmax N] [--register] [--dry-run] [--email <addr>]
```

Steps:
1. Print, then run the command for the subcommand the user asked for.
2. Print the script's own output verbatim. For `run`, summarize in plain text as well:
   per search, how many hits and how many were new, and the titles of the new ones.
3. Say what the numbers mean before offering anything:
   - "new" means **not already in this repo's registry** — not "newly published". A 2019
     paper indexed last week is new to you and will be reported as such.
   - Without `--register`, nothing was added and the next run will report the same
     papers again from a later entry-date floor. Offer `--register`, or
     `/deep-research:pool-add` for a subset.
   - A `truncated` note means the search had more hits than `--retmax` returned; the
     count is a floor, not a total.
4. If a search reports an `error`, say which one and what NCBI returned. The other
   searches in the same run still executed — report their results too.

This command re-runs searches; it does not screen, retrieve, or extract anything. Turning
new hits into evidence is `/deep-research:summarize-set` or a full `deep-research` run.
