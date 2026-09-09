# deep-research — usage

Operator's manual. `SKILL.md` is the agent-facing router; this file is for the person running
the skill.

## What it does

PubMed-centred literature research, end to end: protocol → search → screen → full-text
acquisition → extraction → appraisal → synthesis → verified report, promotable into a wiki.

**Not**: a meta-analysis (no pooled effects, no I², no forest plots — synthesis is
effect-direction tabulation plus explained conflict), a paywall bypass (no credentials, no
proxies, no scraping around access — unobtainable text is quarantined and handed back to you),
or a tool for summarising a single paper / general web research.

## Invoking it

Ask in the session — triggers on "literature review", "systematic review", "evidence
synthesis", "what does the evidence say about X", "deep research on X", "PubMed search",
"critical appraisal", or a request to screen/extract/appraise/write up papers with citations.

Stage 0 asks only for what it doesn't already know, and never re-asks what's already in the
run's `config.json`:

| Asked | Detail |
|---|---|
| Question | Restated back as PICO/PECO before anything runs |
| Profile | `fast` / `standard` / `systematic` / `max` (table below), or scope/rigor/gates individually |
| Target wiki | Run directory and PDF library live inside it; a missing wiki is confirmed with you first |
| Filters | Years, authors, journals, article types, species/age, language, OA-only |
| Outputs | `report.md` always; optionally HTML, Quarto PDF/docx, OKF bundle promotion |

## Profiles

| Profile | scope | gates | max_articles | For |
|---|---|---|---|---|
| `fast` | narrow | none | 10 | Quick read of the field. PubMed MCP only, report only |
| **`standard`** (default) | medium | protocol+strategy | 25 | The normal case. Adds E-utilities |
| `systematic` | wide | both | 60 | Defensible review. Adds Europe PMC, dual screening, PRISMA log, full verifier |
| `max` | max | both | 100 | Widest sweep. Adds guidelines/grey-lit/web connectors |

Overridable field-by-field in the run's `config.json`.

Orthogonal to profile: set `pipeline.stop_after_stage: 5` to run search → screen → retrieve →
extract and then halt — no appraisal, no report, no OKF promotion. Useful for building up the
shared paper pool (below) for a topic before deciding whether it's worth a full appraised
review. Resume the same run later with that field cleared to continue into appraisal.

## The pipeline

Every run walks the same nine stages; the profile only changes scope and where it stops to ask.

```
0 Configure   → question, profile, wiki, filters, outputs; check connector authorization
1 Protocol    → PICO/PECO, numbered inclusion/exclusion criteria
2 Search      → seed candidates from the wiki pool, then 4–8 orthogonal PubMed queries, logged
3 Screen      → dedupe, then title/abstract triage against the numbered criteria
4 Retrieve    → walk the open-access ladder for full text; quarantine what it can't get
5 Extract     → one subagent per paper: design, N, I/C, outcomes, funding/COI, quotes
6 Appraise    → one subagent per paper: RoB2 / ROBINS-I / Newcastle-Ottawa / AMSTAR-2 → GRADE
7 Synthesize  → main thread: effect-direction tabulation, agreement/conflict, gaps, hypotheses
7b Digest     → compress the report into a short wiki-ready summary
8 Publish     → assemble → verify → render → promote to the wiki (if requested)
```

Stage 4's quarantine is the one place the run stops mid-pipeline to talk to you — see below.
Every other stage boundary is just a progress update, not a question.

## Selection & appraisal criteria

Stage 1 frames question as **PICO** (Population, Intervention, Comparator, Outcome) or
**PECO** (Exposure instead of Intervention, for observational/etiological questions), then
turns it into numbered inclusion/exclusion criteria used for screening in Stage 3.

Stage 6 appraises each paper with the tool matching its design — never one-size-fits-all:

| Design | Tool |
|---|---|
| Randomized trial | RoB2 (Risk of Bias 2) |
| Non-randomized/observational study of an intervention | ROBINS-I |
| Cohort / case-control | Newcastle-Ottawa Scale (NOS) |
| Systematic review | AMSTAR-2 |

Per-outcome certainty is then rated with **GRADE** (High/Moderate/Low/Very low), which can
downgrade for risk of bias, inconsistency, indirectness, imprecision, or publication bias.

## Where things live

```
<wiki>/outputs/deep-research/<slug>/   # the run
  config.json  engine.log  taskboard.jsonl  corpus.jsonl
  inputs/      workspace/   outputs/
  missing.md   inbox/
<wiki>/assets/papers/                  # shared PDF library, all runs (rung 0)
  index.json                           # versioned; the PDFs themselves are gitignored
  pool.jsonl                           # shared extraction/appraisal pool, all runs
<wiki>/research/                       # the OKF bundle (promoted concepts)
  index.md  log.md  studies/  claims/  appraisals/  gaps/  hypotheses/  …
```

The skill directory stays code-only; data lives in the wiki. **`<wiki>/wiki/` is never written
to** — that belongs to wiki-manager (OKF 0.1). deep-research owns `<wiki>/research/` only
(spec in `references/okf-bundle.md`); cross-links from `wiki/` into `research/` are yours to make.

Run `python3 scripts/library.py init --wiki <root>` once per wiki to set up `assets/papers/`.

## Outputs

| Output | Command | Good for |
|---|---|---|
| `outputs/report.md` | always | The primary deliverable: evidence, conflicts, certainty, gaps, hypotheses, citations |
| `outputs/digest.md` | Stage 7b | Short summary used as the body of `<wiki>/research/reviews/<slug>.md` |
| `outputs/report.html` | `html_report.py build` | Self-contained shareable page |
| `outputs/prisma.{json,md}` | `corpus.py prisma --out` | Flow numbers: identified → deduped → screened → included |
| PDF / docx | `render.py all --formats pdf,docx` | Citable document via Quarto |
| `outputs/verification.json` | `verify.py run` | Audit: citations resolve, records complete, hypotheses not phrased as findings |
| `<wiki>/research/` concepts | `okf.py promote --run-dir <dir> --wiki <root>` | Durable wiki knowledge |
| `<wiki>/assets/papers/pool.jsonl` | `pool.py sync --run-dir <dir>` (automatic at end of Stage 5/6) | Cross-run extraction/appraisal reuse |
| Wiki-wide `refs.bib` | `pool.py bib --wiki <root> --out <path>` | BibTeX for every paper ever pooled, any run, for publications |

OKF promotion requires `outputs/digest.md` plus a completed `digest:slug:report` taskboard
receipt — the full report stays the audit trail, the digest becomes the wiki concept body.

## Quarantine → inbox → resume

The workflow you'll actually hit. When a paper's full text isn't obtainable through the OA
ladder, it's quarantined; the run keeps walking the ladder for every remaining record and only
surfaces the result once acquisition has been attempted for **all** selected records.

1. **The run alerts you and asks**: a consolidated table (title, PMID, DOI, PMCID, rung reached,
   links) for every quarantined record, the path `<run-dir>/inbox/`, and a direct question of
   whether you can supply any of them.
2. In `systematic`/`max` this is a hard gate — extraction doesn't start until resolved.
   In `fast`/`standard` you may answer to continue without them; the run proceeds and marks the
   synthesis **PROVISIONAL**.
3. **Drop PDFs into `<run-dir>/inbox/`** (any filenames) and rerun the skill, or run
   `library.py ingest-inbox --run-dir <dir>` directly.
4. Matching: DOI regex on page-1 text → exact DOI match; failing that, fuzzy title match
   (ratio ≥ 0.85, never for titles under 25 characters). Unmatched PDFs stay in `inbox/`,
   listed with a reason — never guessed onto a record.
5. Matched PDFs are filed into `<wiki>/assets/papers/` (sha256 dedupe); the run then extracts,
   appraises and re-synthesizes the newly available studies, and the report notes which studies
   arrived by manual supply.

Use `--no-apply` on `ingest-inbox` to preview matches without rewriting `corpus.jsonl`.

## Shared paper pool — never extract the same paper twice

`<wiki>/assets/papers/pool.jsonl` is a second, wiki-wide store next to the PDF library
(`index.json`): one record per paper (keyed by the same `pmid`/`doi`/`pmcid` `evidence_id`
used everywhere), carrying full bibliographic metadata plus a *pointer* to whichever run's
`workspace/extractions/` and `workspace/appraisals/` files actually hold that paper's
structured extraction/appraisal. The heavy content stays inside the run that produced it —
only the pointer and the biblio are shared.

- **Automatic.** Every run calls `pool.py sync` when Stage 5 (and again Stage 6) finishes — no
  separate step to remember.
- **Seed before external search.** At the start of Stage 2, run
  `python3 scripts/pool.py seed --run-dir <dir> --wiki <root>` to add matching pooled papers to
  the new run's `corpus.jsonl` with `source: pool` and `first_seen_query: pool-seed`. They are
  candidates only: Stage 3 still screens them against the new question before retrieval or reuse.
- **Reuse.** Before dispatching an extraction or appraisal subagent, the run calls
  `pool.py reuse --run-dir <dir> --wiki <root> --pmid <pmid>`; a hit copies the other run's
  result in *and* re-registers the snapshot(s) its spans cite into this run's own
  evidence-kernel store (content-addressed by `sha256(url+text)`, so the copy is byte-identical
  and gets the same `source_id`) — the reused quotes verify locally, not just for display. A
  paper researched once, in any run, is never re-extracted by a later run on a different
  question, and its citations stay fully auditable.
- **BibTeX.** `python3 scripts/pool.py bib --wiki <root> --out refs.bib` emits one consolidated
  `.bib` covering every paper ever pooled in that wiki — not just one run's included set — ready
  to cite in a manuscript. `--select appraised` narrows it to papers that also have an appraisal.
- **Build a pool without appraising.** Set `pipeline.stop_after_stage: 5` (Profiles, above) to
  run search/screen/retrieve/extract only; the pool still fills in, appraisal and synthesis just
  don't run. Good for surveying a topic's extractable literature before committing to a full
  systematic review.
- **Freshness stays honest**: a reused snapshot is logged as a `register` event, never `fresh`
  — the text wasn't retrieved in this run. `verify.py`'s C-FRESH-FETCH reports it as non-fresh
  accordingly, same as any other cached text; the integrity/span checks (C-SNAPSHOT, C-SPAN)
  pass normally since the content is now locally present and hash-verified.

## Resuming an interrupted run

Resume is **by task, not by stage**: every unit of work is a record in `taskboard.jsonl`, and a
rerun continues only the incomplete/failed/blocked tasks — it never redoes completed work or
re-asks anything already in `config.json`. Just point the skill at the same question/wiki, or
say "resume".

```bash
python3 scripts/corpus.py task stats  --run-dir <dir>
python3 scripts/fulltext.py status    --run-dir <dir>
python3 scripts/status.py <run-dir> --table     # PMID/title/authors/PDF status/screen/extract/appraise
python3 scripts/status.py <run-dir> --missing   # records with no full text
```

Never hand-edit `taskboard.jsonl` — `corpus.py task` is its only writer.

## Script reference

All under `scripts/`, all `python3`.

| Script | Purpose |
|---|---|
| `eutils.py` | NCBI E-utilities client: hit counts, query translation, PMIDs, citation chaining |
| `fulltext.py` | The acquisition ladder (rungs 0–7), quarantine, `acquire` / `status` / `resolve-mcp` |
| `library.py` | Shared PDF library at `<wiki>/assets/papers/`: `init`, `lookup`, `add`, `ingest-inbox`, `list` |
| `pool.py` | Shared extraction/appraisal pool at `<wiki>/assets/papers/pool.jsonl`: `seed`, `sync`, `lookup`, `reuse` (carries spans across runs), `bib`, `list` |
| `corpus.py` | `corpus.jsonl` store, dedupe, PRISMA counters, screening ingestion, taskboard CLI |
| `okf.py` | OKF bundle writer/validator for `<wiki>/research/`: `init`, `write`, `promote`, `validate` |
| `render.py` | `report.md` → `refs.bib` + `report.qmd` → `quarto render` (`pdf`, `docx`, `html`, `all`) |
| `html_report.py` | Self-contained HTML deliverable (`build`) |
| `verify.py` | Final consistency pass, writes `outputs/verification.json` (never edits the report) |
| `status.py` | Run status overview: stage progress + corpus table |

## Requirements

- **`python3`** only — no `python` on this machine.
- **Packages**: stdlib + `requests` + `pdfminer`. No pip installs, ever.
- **Binaries**: `pdftotext`, `pdfinfo`, `tesseract` (OCR fallback), `quarto` + `pandoc` (export).
- **`NCBI_API_KEY`** (optional) — 3 req/s without it, 10 with.
- **`DEEP_RESEARCH_EMAIL`** (required by the Unpaywall rung and sent to NCBI per their policy).
- `DEEP_RESEARCH_FIXTURES` / `DEEP_RESEARCH_RECORD` — offline replay/recording for tests.

## Limitations, honestly

- **`max` needs connectors that may not be attached** (Scholar Gateway, Consensus). Missing one
  is named explicitly at Stage 0, with a choice to continue at reduced scope or stop; a
  connector authorized mid-run isn't picked up until a new session.
- **The hypothesis-wall check is lexical, not semantic.** It flags banned phrasings; a pass
  means "no banned phrasing found," not "the wall holds."
- **No pooled estimates** — no meta-analytic effect, no I², no funnel plot.
- **Abstract-only evidence is labelled and never appraised as full text.**
- **Rung 1 needs a coordinator MCP call** (a script can't call the PubMed MCP tool); the ladder
  keeps moving and picks the result up via `resolve-mcp` or a rerun.
- **HTML full-text can be truncated** — the truncation detector demotes short/paywalled pages
  to abstract-only rather than treating a teaser as a paper.
- **Preprints are included at `wide`/`max` and tagged loudly.**

## Testing

`scripts/eval.py` runs fixture-backed evals offline by default (`--live` opts into real PubMed).
`python3 -m unittest discover -s tests` runs the unit suite. Neither needs credentials or network.

```bash
DEEP_RESEARCH_FIXTURES=<dir> python3 scripts/eutils.py esearch --query '...'   # replay
python3 scripts/fulltext.py acquire --run-dir <dir> --corpus <path> --offline  # local rungs only
python3 scripts/okf.py selftest
python3 scripts/verify.py run --run-dir <dir> --wiki <root> --json
```
