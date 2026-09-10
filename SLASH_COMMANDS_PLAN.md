# Slash commands for deep-research — implementation plan

Status: draft, not implemented. Review before building.
Rev 2 — CLI signatures verified against the scripts; see "Corrections" and
"Findings that change the plan" below.

## Problem

`/deep-research` invokes the full SKILL.md Stage 0-8 pipeline narrative (repo/wiki
negotiation, run-dir, output target, report). Small asks — "add 5 papers to the pool",
"summarize this one paper", "check run status" — get forced through that framing even
though the underlying scripts (`registry.py`, `paper.py`, `status.py`, etc.) already
support them standalone with no run/report/output-target required.

Goal: one `.claude/commands/*.md` file per atomic function, each calling the relevant
script directly and explicitly instructing the model NOT to consult SKILL.md's staged
pipeline prose.

## Findings that change the plan (rev 2)

**`paper.py summarize-set` already does bounded discovery.** It takes `--question` /
`--topic` + `--limit N` + `--select-only` + `--overview/--no-overview`. That is very
close to the original ask ("MRS studies on autism, human only, most recent 5") — the
full Stage 0-8 pipeline was never needed for it. Consequence:

- `/dr-summarize-set` moves to priority 1, alongside `/dr-pool-add`.
- The two need a stated boundary, or they will be used interchangeably:
  - `pool-add` = **register only**. Bibliographic records into the registry/pool. No
    reading, no summary, no appraisal. Cheap, fast, no LLM work per paper.
  - `summarize-set` = **discover + read + summarize** (optionally appraise). Expensive
    per paper. Use when you want content, not just a shelf.
  - `--select-only` on summarize-set is the middle ground — worth documenting in both
    command bodies so the choice is obvious at invocation time.

**Namespacing.** Prefix every command `dr-` (`dr-pool-add`, `dr-bib-export`, …). Two
reasons: the `/` menu is a flat namespace shared with `swiftui-*` and any plugin
commands, and `dr-` groups them into one visually contiguous block when scrolling.
Plan below uses the prefixed names.

**Echo-the-command convention.** Each command body should print the exact shell command
it is about to run before running it. Makes the wrapper a teaching tool rather than a
black box — you learn the underlying CLI by using the shortcut, and can drop to raw
scripts whenever the wrapper is too narrow.

## Corrections to rev 1 (verified against the scripts)

| rev 1 said | actually |
|---|---|
| `--filters-json '{"species":"human"}'` | value must be a **list**: `'{"species":["human"]}'`. `_mapped_group` iterates the value; a bare string iterates characters and raises `bad_filter`. Valid keys: `years, authors, journals, article_types, languages, free_full_text, species, ages`; unknown keys are a hard error. |
| `status.py --run-dir <dir>` | `run_dir` is **positional**: `status.py <dir>`. Also has `--table` (no table without it), `--limit N`, `--no-summary`. |
| `verify.py --run-dir <dir>` | `verify.py` has **subcommands**: `run --run-dir <dir>` plus `single-paper-summary` and `paper-summary-set`. Also `--repo`, `--json`, `--markdown <file>`, `--gate/--no-gate`. |
| `paper.py summarize` flags "confirm before writing" | confirmed: `--repo` (required), `--pmid/--doi/--pmcid/--pdf/--evidence-id`, `--project`, `--purpose` (default background), `--audience` (default researcher), `--appraise/--no-appraise`, `--reuse/--no-reuse`, `--force`, `--format md\|html\|both`, `--out`, `--offline`. |

## Location — rev 3: ship as a plugin

Supersedes rev 1/rev 2. Neither earlier option was right:

- global `~/.claude/commands/` — pollutes the user's personal dir, not shippable
- skill-scoped `.claude/commands/` — only active when cwd is the skill dir, defeating
  the purpose

**A plugin solves both.** Files live in the plugin's own repo; commands are available
from every working directory once installed; nothing lands loose in `~/.claude/`.

Verified against installed plugins on this machine (`~/.claude/plugins/marketplaces/caveman`),
not from memory — a plugin carries top-level `commands/` and `skills/` **side by side**:

```text
deep-research/                     (this repo, unchanged in place)
  .claude-plugin/
    plugin.json                    manifest — ONLY this file goes in .claude-plugin/
    marketplace.json               optional: makes the repo its own marketplace
  commands/
    dr-pool-add.md                 (or .toml — both work, see below)
    dr-summarize-set.md
    ...
  skills/
    deep-research/
      SKILL.md                     the existing skill, moved one level down
      scripts/  references/  templates/  ...
```

`plugin.json`, minimal shape (verified):

```json
{
  "name": "deep-research",
  "description": "PubMed-centred literature research, appraisal, and synthesis.",
  "version": "1.0.0",
  "author": { "name": "Christine Ecker" }
}
```

It also accepts a `hooks` block (caveman uses one) if you ever want session hooks.

### Command file format

Two forms work. The `.toml` form seen in caveman:

```toml
description = "Register papers into the pool — no run, no report"
prompt = "..."
```

or plain `.md` with a body + `$ARGUMENTS`, matching the `swiftui-*.md` convention. Pick
`.md` — the bodies here are long (multi-step, with the do-not-read-SKILL.md preamble),
and markdown stays readable where a TOML string does not.

### `${CLAUDE_PLUGIN_ROOT}` — fixes rev 2's hardcoded paths

Rev 2 said to hardcode `/Users/sphache/.claude/skills/deep-research/scripts/...`. That
breaks for anyone else who installs the plugin. Plugins expose `${CLAUDE_PLUGIN_ROOT}`
(caveman's hooks use it). So every command body calls:

```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/registry.py" add --repo ...
```

Portable, and works regardless of where the plugin was installed.

### Invocation and namespacing

Commands become `/deep-research:dr-pool-add`. Note the doubled prefix — since the plugin
name already namespaces them, **drop the `dr-` prefix from rev 2**: the files become
`pool-add.md`, `summarize-set.md`, invoked as `/deep-research:pool-add`. Rev 2's
namespacing rationale is satisfied by the plugin name itself.

### Install

- **local dev**: `claude --plugin-dir /path/to/deep-research`
- **distribution**: add `marketplace.json` to `.claude-plugin/` with
  `{"name": "deep-research", "owner": {...}, "plugins": [{"name": "deep-research", "source": "./"}]}`
  — the caveman repo does exactly this, so one repo is both the marketplace and the
  plugin. Users then run `/plugin marketplace add <owner>/<repo>` followed by
  `/plugin install deep-research@deep-research`.

### Migration gotcha

The existing skill must move from the repo root into `skills/deep-research/`. Everything
that references paths relative to the skill root (`references/`, `scripts/`, `templates/`,
`graphify-out/`, the `CLAUDE.md` note about graphify) needs an audit for hardcoded paths.
Also remove/disable the old `~/.claude/skills/deep-research` checkout after installing as
a plugin, or the skill will be registered twice and shadow itself.

## Shared conventions (all commands)

1. **Repo resolution** — first arg is `--repo <path>`. If omitted:
   - if cwd contains `data/papers/registry.jsonl`, use cwd
   - else error: "no --repo given and cwd is not a repo; run `research.py init <path>`
     first or pass --repo"
2. **Script invocation** — always `python3 /Users/sphache/.claude/skills/deep-research/scripts/<name>.py ...`
   (absolute path, so the command works regardless of cwd).
3. **No pipeline narrative** — every command body opens with an explicit instruction:
   "Do not read SKILL.md. Call the script below directly." This is the whole point of
   the split; omitting it defeats the purpose.
4. **Output passthrough** — print the script's own JSON/table output; don't re-narrate
   it unless the user asks for interpretation.
5. **Wiki mode note** — all commands below assume repo mode (`--repo`). Wiki mode
   (`--wiki <wiki-root>`) is the legacy path; add a `--wiki` variant only if you actually
   use wiki-backed runs. Skip by default.

## Commands

### 1. `pool-add.md` — highest priority, solves the immediate need

```
Add papers to the pool/registry directly. No run, no report, no output target.
Do not read SKILL.md's staged pipeline — call the scripts below directly.

Parse $ARGUMENTS for:
  --repo <path>          required (see repo-resolution convention)
  --query "<text>"       PubMed query, OR --pmid <id>[,<id>...] for explicit IDs
  --n <count>             default 5
  --sort pub_date|relevance   default pub_date
  --human-only            if set, add --filters-json '{"species":["human"]}' to esearch
                          (LIST, not string — a bare string raises bad_filter)
  --years <from>-<to>     optional, maps to the filters-json `years` key

Steps:
1. If --query given:
   python3 <scripts>/eutils.py esearch --query "<text>" [--filters-json '...'] \
     --sort <sort> --retmax <n>
   → collect PMIDs from the result.
2. For each PMID (from step 1, or from --pmid): 
   python3 <scripts>/registry.py add --repo <path> --pmid <pmid>
3. python3 <scripts>/registry.py pool --repo <path>
4. python3 <scripts>/registry.py list --repo <path> --limit <n>
5. Report: N papers added, path to pool.jsonl.
```

Resolved (rev 2): `species` is a supported key, value must be a list. Full key set:
`years, authors, journals, article_types, languages, free_full_text, species, ages`.

Body must also state the boundary: this command registers bibliographic records ONLY.
For content — reading, summarizing, appraising — use `/dr-summarize-set`.

### 2. `bib-export.md`

```
Export BibTeX from the registry. No run needed.
Parse: --repo <path>, --out <file.bib>, --select all|extracted|appraised (default all),
--project <slug> (only with --select appraised).
Call: python3 <scripts>/registry.py bib --repo <path> --out <file> --select <select> [--project <slug>]
```

### 3. `pdf-lookup.md`

```
Check whether a paper is already registered/has a PDF, before re-fetching.
Parse: --repo <path>, one of --pmid/--doi/--pmcid.
Call: python3 <scripts>/registry.py lookup --repo <path> (--pmid|--doi|--pmcid) <value>
```

### 4. `dr-summarize.md` — single paper

```
Summarize exactly one paper (§14 record + export).
Parse: --repo <path> (required), one of --pmid/--doi/--pmcid/--pdf/--evidence-id,
  optional --project, --purpose <background|...>, --audience <researcher|...>,
  --format md|html|both (default md), --out <path>, --no-appraise, --no-reuse,
  --force, --offline.
Call: python3 <scripts>/paper.py summarize --repo <path> --pmid <id> [...]
```
Defaults worth knowing (state them in the body): `--purpose background`,
`--audience researcher`, `--format md`, reuse ON (`--no-reuse` to force fresh work).

### 5. `dr-summarize-set.md` — PRIORITY 1, closest match to "just look at N papers"

```
Summarize a bounded set — explicit IDs, or bounded discovery from a question/topic.
Parse: --repo <path> (required), then EITHER
  discovery: --question "<text>" | --topic "<text>", with --limit <N>
  or explicit: --pmid <id> (repeatable), --doi, --pmcid, --evidence-id,
               --ids-file <f>, --bib <f>, --folder <dir> [--recursive]
Optional: --select-only (pick the set, don't summarize — cheap preview),
  --overview / --no-overview (§15 set manifest, default OFF),
  --strict (one bad identifier fails the whole set; default best-effort),
  plus every common flag from dr-summarize (--purpose/--audience/--format/--out/...).
Call: python3 <scripts>/paper.py summarize-set --repo <path> --question "..." --limit N [...]
```
Body must state: this is the expensive path (reads + summarizes each paper). For a
shelf of bibliographic records only, use `/dr-pool-add`. To see which papers would be
picked before paying for summaries, run with `--select-only` first.

### 6. `dr-status.md`

```
Show stage progress + record table for an existing run. Read-only.
Parse: <run-dir> (POSITIONAL, not --run-dir), optional --table, --missing,
  --limit <n> (default 100), --no-summary.
Call: python3 <scripts>/status.py <run-dir> [--table] [--missing]
```
Note: without `--table` you get the summary only — pass it when the user asks "which
papers am I missing".

### 7. `dr-verify.md`

```
Run consistency checks. verify.py is subcommand-based.
Parse: --run-dir <dir> (required), optional --repo <path>, --json, --markdown <file>,
  --no-gate; and a mode: run (default) | single-paper-summary | paper-summary-set.
Call: python3 <scripts>/verify.py run --run-dir <dir> [--repo <p>] [--json] [--markdown <f>]
```
The `single-paper-summary --summary <f>` and `paper-summary-set --summary-set <f>`
modes verify summary outputs specifically — pair them with dr-summarize/-set so a
summary can be checked without a full run existing.

### 8. `dr-init.md` — NEW (rev 2), prerequisite for everything else

```
Create a standalone research repo (or import one from a wiki).
Parse: <path> (positional), optional --from-wiki <wiki-root>.
Call: python3 <scripts>/research.py init <path> [--from-wiki <wiki-root>]
```
Without this, every --repo command errors. Belongs early in the rollout, not late.

### 9. `dr-project.md` — NEW (rev 2), only if you write manuscripts

```
Manuscript project lifecycle (appraisals are project-scoped, so this matters as soon
as you appraise the same paper for two questions).
Parse: create <slug> --repo <path> | list --repo <path>
Call: python3 <scripts>/research.py project create <slug> --repo <path>
      python3 <scripts>/research.py project list --repo <path>
```

### 10. `dr-help.md` — NEW (rev 2)

One-shot cheat sheet: lists every dr-* command, one line each, plus the pool-add vs
summarize-set boundary and the "which do I want?" decision tree. No script call. Cheap
to write, and it is the discoverability fix for a 10-command namespace.

### 11. `dr-watch.md` — lowest priority, may not fit a non-interactive harness

```
Launch the read-only run-monitor TUI.
Parse: --run-dir <dir>.
Call: python3 <scripts>/watch.py --run-dir <dir>
```
Caveat: this is a TUI (renders/refreshes in place). May not work through a headless
agent harness invocation — confirm before implementing, otherwise skip.

## Rollout order (rev 2)

1. `dr-init` — prerequisite; everything with `--repo` fails without it.
2. `dr-pool-add` + `dr-summarize-set` — the two that answer real asks. Build together
   so the boundary text in each body is written once, consistently.
3. `dr-summarize`, `dr-bib-export`, `dr-pdf-lookup` — small, low risk.
4. `dr-help` — write once the above names are final.
5. `dr-status`, `dr-verify` — need a real run to test against.
6. `dr-project` — only if manuscript projects are actually in use.
7. `dr-watch` — decide whether to build at all (TUI caveat above).

## Testing

The repo has `tests/`. Rather than manual smoke tests, add
`tests/test_slash_commands.py` asserting that every command body's script invocation
parses: extract each `python3 .../<script>.py ...` line from the command markdown and
run it through the script's own `build_parser().parse_args()` with `--help`-safe
fixtures. Catches exactly the class of error rev 1 shipped (positional vs flag, string
vs list) without network calls.

Smoke test `dr-pool-add` end to end once against a throwaway `--repo` in the scratchpad
using the MRS/autism query from this session.

## Resolved in rev 2

- [x] `eutils.py` filter keys — confirmed, `species` takes a list
- [x] `paper.py summarize` / `summarize-set` argument names — confirmed above
- [x] `status.py` / `verify.py` signatures — corrected above

## Still open

- [ ] Confirm `registry.py bib --select appraised` requires `--project` (per
      pool-architecture.md's project-scoped appraisal note) — enforce in the command
      body or let the script error
- [x] Command location — resolved in rev 3: ship as a plugin, not `~/.claude/commands/`
      and not skill-scoped `.claude/commands/`
- [ ] Decide whether to restructure this repo into plugin layout now (skill moves to
      `skills/deep-research/`) or keep the current checkout and develop the plugin on a
      branch until the commands are proven
- [ ] Audit for path assumptions broken by the skill moving down one level
- [ ] Decide whether `--repo` should default from an env var (e.g. `DR_REPO`) so the
      flag can be omitted entirely in day-to-day use
