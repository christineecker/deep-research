---
name: deep-research
description: Thorough PubMed-centred literature research — gather evidence for a scientific question, critically appraise it, synthesise it, and surface gaps and hypotheses. Use when the user asks for a literature review, systematic review, evidence synthesis, "what does the evidence say about X", "deep research on X", a PubMed search, critical appraisal of a body of literature, or wants papers screened, extracted, appraised (RoB2/ROBINS-I/GRADE) and written up with citations. Not for single-paper summaries or general web research.
---

# deep-research

Coordinator skill. You are the **main thread**: you configure the run, design the search,
dispatch bounded subagents, and write the synthesis. Scripts do the network and state work.
Subagents do the per-paper work and hand back one-line receipts.

Read this file, then read only the reference you need for the stage you are in.

| Need | Read |
|---|---|
| JSON contracts for every record and receipt | `references/schema.md` (index → `references/schema/*.md`, one file per record) |
| Query design, MeSH, hedges, orthogonality | `references/search-strategy.md` |
| Full-text ladder, truncation detector, quarantine | `references/acquisition.md` |
| RoB2 / ROBINS-I / NOS / AMSTAR-2 / GRADE | `references/appraisal.md` |
| Effect direction, heterogeneity, conflict, the hard wall | `references/synthesis.md` |
| PRISMA flow, citation format, report skeleton | `references/reporting.md` |
| Wiki bundle frontmatter, taxonomy, validation rules | `references/okf-bundle.md` |
| Snapshots, spans, freshness, the assembler gate | `references/evidence-kernel.md` |
| Subagent prompt blocks | `references/prompts/{screen,adjudicate,extract,appraise,digest}.md` |

---

## Stage 0 — configuration

Load the profile, infer defaults, then ask **only** for missing high-impact choices. Never
re-prompt for a value already in `config.json` unless the user asks to change it.

### Profiles

| Profile | scope | rigor | gates | max_articles | Extras |
|---|---|---|---|---|---|
| `fast` | narrow | fast | none | 10 | report only |
| **`standard`** (default) | medium | standard | protocol+strategy | 25 | report + OKF bundle when a wiki is selected |
| `systematic` | wide | systematic | both | 60 | dual screening, PRISMA log, full verifier, optional Quarto export |
| `max` | max | systematic | both | 100 | + guidelines / grey literature / preprints, connector auth checks |

Orthogonal to profile: **`pipeline.stop_after_stage`** (`config.json`, integer, default unset).
Set it to `5` to build the shared paper pool only — search → screen → retrieve → extract, then
halt: no appraisal, no synthesis, no report, no OKF promotion. Ask about this alongside the
profile pick ("just build/extend the extracted-paper pool for this question, or run the full
pipeline through appraisal and a report?"). A `stop_after_stage: 5` run still runs
`pool.py sync` at the end (Stage 5's normal exit), so its extractions are immediately reusable
by any later run, on this question or another, that hits the same papers. Resuming later with
`stop_after_stage` cleared continues the *same* run into Stage 6 onward — it is a pause point,
not a different pipeline.

### Scope tiers

- **narrow** — PubMed MCP only (`search_articles`, `get_article_metadata`, `find_related_articles`)
- **medium** — + `scripts/eutils.py` (exact boolean/MeSH, hit counts, large result sets)
- **wide** — + Europe PMC REST (indexes bioRxiv / medRxiv / Research Square; preprints tagged loudly)
- **max** — + web: guidelines, grey literature, Scholar Gateway / Consensus connectors.
  Never assume these are available: check at Stage 0 (below) and never fake the coverage.

### Connector preflight — before the first query, every run

Tool availability is a property of **this session**, not of the skill: MCP servers attach at
session start, so a connector authorized ten minutes ago is still absent here until the user
starts a new session. Never carry a claim about what is connected across sessions, and never
copy one out of this file — it would be a fact with an expiry date.

Check your own tool list, once, at Stage 0:

| Source | Look for | Needed by |
|---|---|---|
| PubMed MCP | `mcp__claude_ai_PubMed__*` (`search_articles`, `get_article_metadata`, `get_full_text_article`, `find_related_articles`) | **every** scope; also ladder rung 1 |
| Scholar Gateway | its `mcp__*` tools | `max` only |
| Consensus | its `mcp__*` tools | `max` only |
| claude-in-chrome | `mcp__claude-in-chrome__*` (`navigate`, `read_page`, `get_page_text`, `tabs_create_mcp`) | optional, every scope — ladder rung 7 only (`references/acquisition.md` §4b). If missing, resolve every pending `needs_browser` task with `fulltext.py resolve-browser --status unavailable` so rung 7 doesn't sit pending forever; no gate, no ask. |

`scripts/eutils.py` and Europe PMC are plain HTTPS and need no connector; `narrow` through
`wide` therefore run on PubMed MCP alone.

**PubMed MCP is mandatory, every scope.** If `mcp__claude_ai_PubMed__*` is not in your tool
list, do not proceed at any reduced scope. Tell the user PubMed MCP is missing, that it's
authorized in claude.ai → Settings → Connectors and picked up by a **new** session, and stop —
wait for them to connect and start a new session. Do not offer a workaround that fetches the
same material another way, and do not ask for an authorization code, token, or callback URL
(it's a browser OAuth flow you cannot drive). No "proceed anyway" option for this one.

Scholar Gateway / Consensus (both `max`-only) stay optional: if missing, tell the user by name,
then ask whether to **proceed at a reduced scope now** or **stop and resume after connecting**.
Their call. `max` without them is `wide` with extra steps — say that plainly rather than running
`max` and quietly returning less.

Record what you found in `config.json` under `connectors`, e.g.
`{"pubmed_mcp": true, "scholar_gateway": false, "consensus": false, "checked_at": "<iso>"}`,
and carry it into the report's Methods section: a run that could not reach a source the
profile assumes must say which source and what it means for coverage.

On resume, re-check rather than trusting the recorded value — a resumed run is usually a new
session, which is exactly when availability changes.

### Institutional access (optional, ladder rung 7 only)

Off by default. Ask once per run, alongside the other Stage 0 questions: does the user want a
personal institutional library search (e.g. their university's discovery portal) tried as a
last-resort candidate before quarantine? If yes, ask for its name and discovery search URL and
record `config.json` `institutional_access: {"enabled": true, "name": "<name>", "search_url":
"<url>"}`. If no, or the question isn't asked, `institutional_access` stays absent and rung 7
is OA-only, exactly as documented in `references/acquisition.md` §4b.

This does **not** mean the agent signs in. When rung 7 opens that search and hits an SSO wall,
the coordinator stops and hands the open tab to the human to authenticate themselves — the
agent never types, stores, requests, or transmits the credential. Say this plainly when asking,
so the user knows what "enabled" actually does: their institutional licence, used at their own
discretion, in their own browser session — never automated credential entry.

### Ask, if not already known

Always ask explicitly — never silently default a profile or filter set because the question
"sounds like" a given mode. Present the profile table (with what each one trades off) and wait
for the user's pick rather than inferring `fast`/`standard`/etc. from phrasing.

1. **Question** — restate it back as a PICO/PECO before proceeding.
2. **Profile** — show the profile table above and ask the user to pick one (or set
   scope / rigor / gates individually). Say what the chosen profile implies: `max_articles`,
   whether gates apply, whether dual screening runs, whether it's report-only.
3. **Target wiki** — needed *early*, because the run directory and PDF library live in it.
   Enumerate the wikis directory at runtime; never hardcode the list. Missing wiki →
   confirm creation and location with the user first.
4. **Filters** — years, authors, journals, article types, species/age, language, OA-only.
   **Ask explicitly whether meta-analyses and systematic reviews should be included** in the
   corpus or excluded as an article-type filter — do not assume either way. If included, ask
   whether they should be synthesized alongside primary studies or reported/appraised
   separately (AMSTAR-2 applies to reviews, not RoB2/ROBINS-I). Record the answer in
   `config.json` under `filters.article_types` / `filters.include_reviews`.
5. **Outputs** — `report.md` always; optionally HTML artifact, Quarto PDF/docx, OKF bundle
   promotion. Also ask whether this run should stop after extraction (`pipeline.stop_after_stage:
   5`, see Profiles above) instead of running the full pipeline. A wiki-wide `refs.bib` covering
   every paper ever pooled (not just this run's included set) is always available on demand via
   `python3 scripts/pool.py bib --wiki <root> --out <path>` — mention it once the pool is
   non-empty, it does not need a per-run flag.

### Progress updates — every profile, including `fast`

A run with no visible progress looks stalled even when it isn't. After **every** stage
transition (bump of `config.json`'s `stage`) and at least once per batch of dispatched
subagents, run `python3 scripts/status.py <run_dir>` and post its output (or a short summary of
it — current stage, progress bar, counts) to the user as plain text. This applies to `fast`
profile too: "report only" affects which *artifacts* get produced, not whether the user sees
what stage the run is in. Do not wait until the final report to surface stage progress.

### Run directory

```
<wiki>/outputs/deep-research/<slug>/
  config.json     engine.log     taskboard.jsonl
  events.jsonl    sources/                        # evidence kernel
  inputs/         workspace/     outputs/
  missing.md      inbox/
```

**Reserved directories — scanned by the scripts, one record shape only.** `corpus.py` reads
every `*.json` in these and treats each as a record of that stage's type:

| Directory | Holds, and nothing else |
|---|---|
| `workspace/search/` | one *search result record* per query (`q1.json` …) |
| `workspace/screening/<screener>/` | one *screening verdict* per record (`pmid-*.json`) |
| `workspace/screening/adjudication/` | one *adjudication record* per disputed record |
| `workspace/extractions/`, `workspace/appraisals/` | one record per paper |

Put batch inputs, efetch payloads, rankings and other working files **outside** these — e.g.
`workspace/batches/`, `workspace/fetch/`. A stray file of the wrong shape used to crash
`prisma`/`screen-ingest` with a bare `AttributeError`; it is now skipped with a warning, but
the directory contract is still the rule.

Write `config.json` first: profile, scope, rigor, gates, filters, wiki target, budgets
(`max_articles`, `max_subagents`, `max_parallel`, `max_wall_time`, `max_fulltext_failures`,
`max_ocr_pages`), the `connectors` block from the preflight above, and the stage pointer. Run
`scripts/library.py init --wiki <root>` once per wiki.

**Stage pointer**: bump `config.json`'s `stage` as each stage completes, in the same turn.
It is the only thing a resume trusts to know where the run got to — a live coordinator that
carries the stage in its head leaves the pointer stale and sends the next session back to a
stage that is already done.

**Resume**: if the run directory exists, read `config.json` + `taskboard.jsonl` and continue
incomplete/failed tasks. Do not restart the stage. Do not re-prompt.

---

## Pipeline

| # | Stage | Runs in | Model | Writes |
|---|---|---|---|---|
| 1 | Protocol | main | main | `outputs/protocol.md` |
| 2 | Search | main, scripts only | — | pool-seeded corpus records, then `workspace/search/<query_id>.json` |
| 3 | Screen | subagents, batched | sonnet | `workspace/screening/<screener>/pmid-*.json` |
| 4 | Retrieve | main + `fulltext.py` | — | corpus `fulltext` block, `missing.md` |
| 5 | Extract | subagents, 1/paper | opus | `workspace/extractions/pmid-*.json`, pool synced. **Halts here if `stop_after_stage: 5`** |
| 6 | Appraise | subagents, 1/paper | opus | `workspace/appraisals/pmid-*.json`, pool synced |
| 7 | Synthesize | main only | main | `outputs/report.md` |
| 7b | Digest | subagent, 1 | opus | `outputs/digest.md` |
| 8 | Verify / report | main | main | `outputs/verification.json` |

**Stage 1 — protocol.** PICO/PECO, *numbered* inclusion/exclusion criteria (screening cites
these ids), limits, planned search. Template: `templates/protocol.md`. If gates include
`protocol+strategy`, show the protocol *and* the search strategy to the user and wait.

**Stage 2 — search.** First seed from the wiki-wide paper pool:
`python3 scripts/pool.py seed --run-dir <run_dir> --wiki <root>`. It scores
`<wiki>/assets/papers/pool.jsonl` against the run question/PICO/filters and upserts likely
papers into `corpus.jsonl` with `source: pool` and `first_seen_query: pool-seed`. These are only
candidates: Stage 3 still screens them against the current protocol, and Stage 5/6 later use
`pool.py reuse` to copy prior extraction/appraisal files instead of redoing subagent work.
Then design 4–8 genuinely orthogonal queries per `references/search-strategy.md` — not eight
near-duplicates. Execute via `scripts/eutils.py esearch`; log every query string, the
NCBI-translated query, and the hit count. Citation chaining via `eutils.py elink`. At
`wide`/`max`, add Europe PMC and web sources. Duplicate queries are detected and skipped by
`corpus.py`. Then `eutils.py efetch` → `corpus.py add` → dedupe.

**Stage 3 — screen.** Dedupe first (PMID → DOI → normalized title). Title/abstract triage
against the numbered criteria; include/exclude/unclear + first failing criterion; retraction
and expression-of-concern flags. Batch PMIDs across subagents; prompt block is
`references/prompts/screen.md`. At `systematic`/`max`: **two independent screeners** plus an
adjudicator on disagreement (`references/prompts/adjudicate.md`); log the agreement rate in
the PRISMA log. Cap the included set at `max_articles`, choosing by protocol relevance, and
say in the report how the cap was applied.

**Stage 4 — retrieve.** `scripts/fulltext.py acquire` walks the ladder (see
`references/acquisition.md`). Rung 1 is the PubMed MCP `get_full_text_article` tool, which a
script cannot call — the script appends `needs_mcp` tasks to
`workspace/retrieve/mcp-tasks.jsonl` and keeps walking the ladder, so the run never blocks.
**You** call the MCP tool, write the text to the task's `result_path`, then
`fulltext.py resolve-mcp` (or re-run `acquire`, which picks it up).
Rung 7, the last rung before quarantine, is the same handoff shape for the claude-in-chrome
MCP tools (`references/acquisition.md` §4b): the script appends `needs_browser` tasks to
`workspace/retrieve/browser-tasks.jsonl`. **You** search for and open a freely-accessible copy
— OA content only, never past a login wall, paywall, or captcha (see invariant 9 below) —
extract the visible text, write it to the task's `result_path`, then `fulltext.py
resolve-browser` (or re-run `acquire`).
Record `source_tier` + `access_route` on every record. Quarantined papers go to `missing.md`.
Never ask the user for permission per record — just quarantine and keep walking the ladder for
every remaining record. `acquire` fetches records concurrently (`--workers`, default 4, or
`budgets.max_parallel`); per-host rate limits hold regardless, and `--offline` runs serially.

Once acquisition has been attempted for every selected record (stage 4 fully finished), handle
`missing.md` the same way in every profile: **halt before stage 5** and ask the user — a single
consolidated table (Title, PMID, DOI, PMCID, rung reached, links) — render DOI as
`https://doi.org/<doi>` and PMID as `https://pubmed.ncbi.nlm.nih.gov/<pmid>/` so both are
clickable, never bare identifiers — plus the exact path to drop PDFs
into (`<run_dir>/inbox/`), and an explicit question of whether they can supply any of them. This
is a question, asked once for the whole quarantine list, never per record and never mid-ladder.
For `systematic` and `max`, this is a hard gate — stage 5 does not start until every quarantined
record is resolved. For `fast` and `standard`, the user may answer "continue without it"; if they
do, proceed to stage 5 with the affected records tagged as missing and the synthesis marked
**PROVISIONAL**. The user drops PDFs/HTML into `inbox/`, `python3 scripts/library.py ingest-inbox
<run_dir>` matches them in, and `fulltext.py acquire` (or a rerun) clears `missing.md`.

**Stage 5 — extract.** Before dispatching a subagent for a paper, try the shared pool:
`python3 scripts/pool.py reuse --run-dir <run_dir> --wiki <root> --pmid <pmid>` (fall back to
`--doi`/`--pmcid` when no PMID). A `matched: true` with a non-null `extraction` means another
run already extracted this exact paper — `reuse` has already copied that file into this run's
`workspace/extractions/pmid-*.json` (tagged `"reused_from": "<run-slug>"`) **and** re-registered
every snapshot its spans cite into this run's own evidence-kernel store (same `source_id`,
content-addressed, so it resolves byte-identically here — `references/evidence-kernel.md`), so
its quotes/spans verify locally exactly like a freshly extracted paper's. Skip the subagent and
log the reuse. Otherwise dispatch as normal: one subagent per paper, opus,
`references/prompts/extract.md`. Design, N, population, I/C, outcomes with effect + CI +
direction, funding/COI, limitations, quotes with anchors. `evidence_basis` is `abstract_only`
whenever no full text was obtained. When the stage completes, run
`python3 scripts/pool.py sync --run-dir <run_dir>` so this run's fresh extractions become
reusable by every future run, then `python3 scripts/status.py <run_dir> --table` and post the
table to the user (note which rows were reused vs. freshly extracted) — it shows which papers
were downloaded, in what format (PDF/HTML/Text), and whether extraction ran, so the user can
see corpus coverage before appraisal.

A reused snapshot carries a `register` event, never `fresh` (R22 — the text was not retrieved
in this run). `verify.py`'s C-FRESH-FETCH reports it as non-fresh accordingly, exactly like any
other cached/registered text; C-SNAPSHOT and C-SPAN pass normally since the content is now
locally present and hash-verified.

If `config.json`'s `pipeline.stop_after_stage` is `5`: stop here. Do not dispatch Stage 6, do
not synthesize, do not render a report, do not promote to `<wiki>/research/`. Tell the user the
pool now holds N extracted papers for this question and that a later run (same question, cleared
`stop_after_stage`, or an unrelated question that cites the same papers) will reuse them via the
lookup above instead of re-extracting.

Stage 6 does not start until stage 5 has actually finished for every record eligible under the
profile's quarantine policy — `status.py --table` shows no row still pending extraction. If a
fresh quarantine surfaces during extraction itself, apply the same rule as stage 4: show the
table, point at `inbox/`, and ask. `systematic` and `max` do not proceed until resolved; `fast`
and `standard` may proceed provisionally if the user says to continue without it.

**Stage 6 — appraise.** Same reuse gate as Stage 5: `pool.py reuse` (harmless to call again
per paper even if Stage 5 already reused its extraction — it's idempotent and this time checks
for a resolving `appraisal_path`). A hit copies `workspace/appraisals/pmid-*.json` with
`"reused_from"` set, its snapshots re-registered the same way, and skips the subagent;
otherwise dispatch as normal. One subagent per paper, opus, `references/prompts/appraise.md`.
Tool by design (RoB2 / ROBINS-I / Newcastle-Ottawa / AMSTAR-2 / none), then GRADE domains.
Abstract-only records are **not** appraised as if full text. Run
`python3 scripts/pool.py sync --run-dir <run_dir>` again when the stage completes, so the
appraisal pointer joins the extraction pointer already synced.

**Stage 7 — synthesize.** Main thread only. Effect-direction tabulation, agreement and
conflict *with an explanation of why*, certainty, gaps, and a hard-walled hypotheses section.
See `references/synthesis.md`.

**Stage 7b — digest.** One subagent, opus, `references/prompts/digest.md`. Compresses the
finished `outputs/report.md` (§1, §8, §9, §10, §11, §12, §15 only) into `outputs/digest.md`:
answer-first paragraph, a categorical outcomes table (outcome/direction/certainty/studies), and
a `## Open Questions` section merging gaps and hypotheses — each with what would resolve it. No
new research, no re-derived judgement, same GRADE wordings and hard-walled hypothesis
separation as the source report. This is the body content `okf.py promote` writes into
`research/reviews/<slug>.md`; the full report stays linked from it as the audit trail, it does
not replace §0-§16 for methods/PRISMA/provenance detail.

**Stage 8 — assemble, verify, publish.** Fixed order:

```
assemble.py run  ->  verify.py run  ->  render.py / html_report.py  ->  okf.py promote --check  ->  okf.py promote
```

`okf.py promote` uses `outputs/digest.md` as the `reviews/<slug>.md` concept body; promotion
blocks if Stage 7b has not produced a `completed` receipt, the same way it already blocks on a
failed verifier pass.

`assemble.py` decides which artifacts are admissible and writes `outputs/result.json`;
`verify.py` runs the 12 report checks plus the four kernel checks and writes
`outputs/verification.json`; `okf.py promote --check` re-verifies integrity and writes nothing;
only then does promotion write concepts.

The **evidence-kernel gate is off by default** until a live end-to-end run has passed. Gate off,
the kernel checks still run and are still reported — they warn instead of failing. Turn it on
with `verify.py --gate` / `assemble.py --strict`, or `gates.evidence_kernel: true` in
`config.json`.

**Tamper is never downgraded.** A snapshot hash mismatch or an excerpt that does not match its
re-slice is a hard failure and blocks promotion with the gate off. That is not a rollout
concern. Legacy span-less records are `unverified` — a different thing from tampered, and
reported as such.

---

## Delegation rules

- Subagents receive **bounded** inputs: one paper (or one batch of PMIDs), the criteria, and
  the output path. Never the whole corpus.
- A subagent **writes its own result file** and returns **only** the one-line receipt
  `{schema_version, task_id, status, output_path, summary}` — never raw paper text, never the
  full JSON. This is what keeps main context small over a long run.
- Subagents **cannot spawn subagents**.
- **Subagent shell hygiene.** Verify written files with `find <dir> -name '<glob>' | wc -l`
  or a `python3` one-liner — never `ls`. A user alias (`ls` -> `eza`) hung indefinitely on an
  iCloud-backed run directory and wedged the subagent's shell for 45 minutes after its work
  had finished; the agent still returned its receipt, so the run looked clean while the
  process lingered at 0% CPU. Do not end a task with a decorative listing.
- The output directory is **shared across a stage's subagents**. Files from sibling batches
  are expected; a subagent must not count, audit, or comment on files it did not write.
- Launch independent subagents in a single message so they run concurrently, up to `max_parallel`.
- Malformed subagent JSON → retry once, quoting the schema error → then mark failed/blocked.
- Models: screening → sonnet; extraction, appraisal and digest → opus; synthesis and
  verification → main model.

## Task board

Every unit of work is a record in `taskboard.jsonl`. **Never hand-edit it.** All transitions go
through:

```bash
scripts/corpus.py task claim|complete|fail|block|list|next --stage <stage> [...]
```

which computes `inputs_hash`, stamps timestamps and appends. Pass the subagent receipt's
`summary` to `task complete --summary` so the one-line result is preserved on the board
(`task list` prints it under the row) instead of living only in the coordinator's context. Completed outputs are immutable
unless `inputs_hash` changes. Failed tasks retry independently. `task_id` grammar:
`<stage>:<key-kind>:<key>` (e.g. `extract:pmid:12345678`).

## Execution guardrails

- Budgets `max_subagents`, `max_parallel`, `max_wall_time`, `max_articles`,
  `max_fulltext_failures`, and `max_ocr_pages` live under `config.json`'s `budgets` object and
  are enforced, not aspirational. `max_wall_time` is **seconds** as a bare number, or a suffixed string
  (`90m`, `3h`, `1d`). Never write a bare number meaning minutes.
- Duplicate PubMed / E-utilities / web queries are detected before execution and skipped or
  merged into the existing result.
- **No-progress guard**: repeated identical tool calls, or repeated task assignment with no
  newly completed or blocked work, → write diagnostics to `engine.log`, stop dispatching, and
  move to verification with a provisional report. Check with `corpus.py guard`.
  Use `corpus.py guard --no-record` for a **status check**: every recording call appends a
  probe, so polling the guard while a run is legitimately parked (waiting on the user, or
  between stages) manufactures the very flat window it looks for. A board with nothing pending
  and nothing active now reports `idle: true` and `dispatch-next-stage-or-finish` rather than
  a stall — idle is not the same as stuck.

## Health alerts — tell the user when something is not working

A degraded run looks exactly like a healthy one unless you say otherwise. Silence is a bug,
not tact. **Surface each of the following to the user in the turn you discover it**, in plain
words, with the numbers — never bury it in a stage summary and never let it surface only at
the end.

| Condition | How you find it | What the user must be told |
|---|---|---|
| Ladder rung 1 unreachable | `acquire` reports `needs_mcp > 0` and no PubMed MCP tool is in your tool list | Rung 1 cannot run this session; the best source for paywalled records is unavailable. Resolve each task `--status unavailable` rather than leaving it pending, and say the shortfall is partly infrastructure, not only paywalls |
| Majority without full text | `quarantined + abstract_only > half` of the selected set | Extraction quality is materially limited; say so **before** extracting, and mark the synthesis provisional |
| Any quarantined record | `missing.md` is non-empty | Once acquisition has been attempted for every selected record (not per-record, mid-run), present the full quarantine list as one table — Title, PMID, DOI, PMCID, rung reached, links — the exact path to drop PDFs into (`<run-dir>/inbox/`), and ask whether the user can supply any of them. `systematic` and `max` halt before stage 5 until the user supplies the missing PDFs/HTML and `missing.md` is cleared via `ingest-inbox` + rerun; `fast` and `standard` may proceed with a PROVISIONAL synthesis if the user answers to continue without them. |
| A connector/tool the profile assumes is unauthorized | Stage 0 connector preflight; `config.json` `connectors` | Name the server, say it is authorized in claude.ai → Settings → Connectors and picked up by a **new** session, and ask: reduced scope now, or stop and resume connected? Never fake the coverage |
| A script crashes or a check cannot run | non-zero exit, traceback | Quote the actual error. Do not paraphrase a traceback into "some issues" |
| A budget is hit | `max_articles`, `max_fulltext_failures`, `max_wall_time` | Say which budget, what it cut, and what the run would look like without it |
| No-progress guard trips | `corpus.py guard` | Stop dispatching, report the diagnostics, move to a provisional report |
| Verifier check fails | `verify.py run` | Report which check id failed and what it blocks. A failing kernel check is never rounded down to "passed with warnings" |

Two rules that override any instinct to keep the run looking clean:

1. **Never report a stage as complete when part of it silently did not run.** "10 of 25 full
   text" is the result; "acquisition complete" is not.
2. **An empty result is a finding, not a failure to hide.** If a query yields 7 records where
   you expected hundreds, say the literature is thin — do not quietly widen the query until
   the number looks respectable.

## Failure semantics

| Failure | Response |
|---|---|
| Acquisition failure | Quarantine the source; after the full attempt, halt and ask the user for the PDF in every profile — `systematic`/`max` require it before extraction, `fast`/`standard` may continue provisionally on the user's say-so |
| Malformed subagent JSON | Retry once with the schema error, then failed/blocked |
| Reporter/export failure | Preserve `outputs/report.md`, record in `engine.log` |
| Authorization / paywall policy | **Fail closed.** Never fall back to circumvention |
| OKF validation failure | Keep the report, block wiki promotion, write `outputs/okf-validation.md` |

## Quarantine → inbox → resume loop

Unobtainable full text → `missing.md` with PMID, DOI, PMCID, title, journal and direct
PubMed/DOI/PMC links. Never ask the user for permission per record — quarantine it and keep
walking the ladder for every remaining record; that is stage 4 finishing, not the pipeline
finishing. Only once acquisition has been attempted for every selected record does the run
surface the result: a single table (Title, PMID, DOI, PMCID, rung reached, links) covering all
quarantined records at once, the exact path to drop PDFs into — `<run-dir>/inbox/` — and a
question asking whether the user can supply any of them. In `systematic` and `max`, this is a
hard gate: extraction (stage 5) does not start while `missing.md` is non-empty, regardless of the
answer. In `fast` and `standard`, if the user answers to continue without the missing PDFs,
extraction proceeds and the synthesis is marked PROVISIONAL. The user drops PDFs there; `scripts/library.py
ingest-inbox` matches each PDF to its quarantined record (DOI regex `10\.\d{4,}/\S+` against
page-1 `pdftotext` output, else fuzzy title), files it into `<wiki>/assets/papers/`; a rerun of
`acquire` clears the resolved records from `missing.md`. The report states which studies arrived
by manual supply.

---

## Invariants — non-negotiable

1. No PubMed-backed claim without a PMID **actually retrieved this run**.
2. No claim from any source without a retrieved `evidence_id`: PMID, DOI, PMCID, preprint DOI,
   guideline URL, report URL, or bundle-relative OKF concept path.
3. Abstract-only vs full-text is tagged on **every** extraction; abstract-only is never
   appraised as if full text.
4. Null and negative findings are **actively searched for**, not just whatever surfaced.
   Screeners must not exclude on outcome positivity.
5. Conflicts are surfaced **with an explanation**, never averaged away.
6. "New insights / hypotheses" is hard-walled from "what the evidence shows".
7. Retracted / Expression-of-Concern papers are flagged at screening.
8. PubMed MCP attribution is honored: cite PubMed and DOIs.
9. **No paywall circumvention of any kind** — no credentials, no saved session/cookie jar, no
   institutional/VPN/EZproxy/Shibboleth proxy, no captcha solving, no pirate mirrors, no
   retrying a 401/402/403 by another route. Rung 7's browser handoff (`references/acquisition.md`
   §4b) may render a JS-gated page, but only to reach content with no access control at all —
   a login wall, paywall, or captcha there means `unavailable`, never a workaround. The one
   exception is the opt-in `institutional_access` setting (Stage 0, off by default): even then
   the agent never handles the credential — it stops at any SSO wall and the human authenticates
   themselves in their own browser tab, on their own licence. The OA ladder is the whole ladder,
   plus that one human-gated exception the user explicitly switched on. Fail closed.
10. Bundle concepts follow `references/okf-bundle.md`; PubMed-derived concepts preserve full
    PubMed bibliographic metadata in frontmatter.
11. Per-claim attribution uses markdown footnotes keyed to `sources[].id`. A body-only
    citation list is not acceptable.
12. `<wiki>/research/` is deep-research's bundle. **`<wiki>/wiki/` is never written to.**
13. Standard markdown links are the graph layer; Obsidian wikilinks are additive only.
14. Unknown values are `null`. Never invent a bibliographic field, a number, or a citation.
15. Whenever a PDF is handed to the user, its DOI is stated alongside the PMID (when a DOI
    exists for that record); if no DOI exists, say so rather than omitting the line.

## Scripts

| Script | Job |
|---|---|
| `scripts/eutils.py` | esearch / efetch / elink, throttle, retry, hit counts, translated query |
| `scripts/fulltext.py` | acquisition ladder, resumable, truncation detector |
| `scripts/library.py` | `<wiki>/assets/papers/` PDF index, matching, inbox ingestion |
| `scripts/pool.py` | `<wiki>/assets/papers/pool.jsonl` shared extraction/appraisal pool (`seed` before search; `reuse` carries spans across runs via the evidence kernel) + wiki-wide BibTeX |
| `scripts/corpus.py` | corpus.jsonl, dedupe, PRISMA counters, `task` CLI, guards |
| `scripts/okf.py` | bundle concept writer + validator |
| `scripts/render.py` | report.md → .qmd + refs.bib → quarto render (pdf/docx) |
| `scripts/html_report.py` | self-contained HTML deliverable: evidence table, effect-direction chart, per-study cards |
| `scripts/verify.py` | citation / corpus / OKF consistency checks |
| `scripts/eval.py` | fixture-based smoke harness; `--live` for real PubMed |
| `scripts/store.py` | evidence kernel: immutable snapshots, spans, event log, freshness |
| `scripts/source.py` | `fetch` / `read` / `spans` / `local` over the snapshot store |
| `scripts/assemble.py` | admissibility gate → `outputs/result.json` |
| `scripts/watch.py` | read-only TUI: attach to a live or finished run |

All scripts are `python3` (there is no `python` on this machine), stdlib + `requests` +
`pdfminer` only. **No pip installs.**
