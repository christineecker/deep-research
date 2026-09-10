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
| Target: wiki or standalone repo | Run directory, PDF/paper store, and shared pool all live inside it; see "Two ways to run this" below |
| Filters | Years, authors, journals, article types, species/age, language, OA-only |
| Outputs | `report.md` always; optionally HTML, Quarto PDF/docx, OKF bundle promotion |

## Two ways to run this

Everything above and below works identically either way — same stages, same subagents, same
report. Only where the run, PDF/paper store, and shared cross-run pool live differs.

| | **Wiki mode** (default) | **Standalone repo mode** |
|---|---|---|
| Setup | A wiki-manager wiki must exist or be created | `python3 scripts/research.py init <path>` once |
| Run lives at | `<wiki>/outputs/deep-research/<slug>/` | `<repo>/runs/<slug>/` |
| Shared pool | `<wiki>/assets/papers/pool.jsonl` | `<repo>/data/papers/registry.jsonl` (`pool.jsonl` is a regenerated view of it) |
| PDF/paper intake | `library.py` | `registry.py add` / `add-pdf` / `import-bib` / `import-folder` |
| Manuscript output | Promoted into `<wiki>/research/` (`okf.py promote`) | `<repo>/projects/<slug>/` (`manuscript.qmd`, `refs.bib`, `synthesis.md`) — create with `research.py project create <slug> --repo <path>` |
| Needs wiki-manager? | Yes | No, ever |

Pick standalone repo mode when you don't want a generated wiki at all, or you're building a
manuscript that lives in its own repo. `research.py export wiki` can copy a repo's pool and one
project's bundle into a wiki afterwards if you change your mind — a one-way courtesy copy, not a
migration. `pool.py migrate --from-wiki <wiki> --repo <path>` goes the other way: pulls an
existing wiki's pool (papers, extractions, appraisals) into a new standalone repo. Full layout
and command mapping: `references/pool-architecture.md`.

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
0 Configure   → question, profile, wiki/repo, filters, outputs; check connector authorization
1 Protocol    → PICO/PECO, numbered inclusion/exclusion criteria
2 Search      → seed candidates from the shared pool, then 4–8 orthogonal PubMed queries, logged
3 Screen      → dedupe, then title/abstract triage against the numbered criteria
4 Retrieve    → walk the open-access ladder for full text; quarantine what it can't get
5 Extract     → one subagent per paper: design, N, I/C, outcomes, funding/COI, quotes
6 Appraise    → one subagent per paper: RoB2 / ROBINS-I / Newcastle-Ottawa / AMSTAR-2 → GRADE
7 Synthesize  → main thread: effect-direction tabulation, agreement/conflict, gaps, hypotheses
7b Digest     → compress the report into a short summary
8 Publish     → assemble → verify → render → promote to the wiki, or leave as the repo's project deliverable
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

The appraisal is result-specific, not a whole-paper quality grade. The appraiser first fixes the
outcome, effect of interest, follow-up window, comparator, and evidence basis, then works the
tool's canonical domains from source spans. Mixed-design reviews stay stratified in synthesis:
RCTs, ROBINS-I studies, NOS studies, and AMSTAR-2 reviews are not averaged into a single quality
bucket. Designs without an in-scope instrument, such as diagnostic accuracy, descriptive
cross-sectional, guidelines, and narrative reviews, are recorded as `tool: "none"` and reported as
unappraised by this skill rather than forced into the wrong checklist.

Per-outcome certainty is then rated with **GRADE** (High/Moderate/Low/Very low), which can
downgrade for risk of bias, inconsistency, indirectness, imprecision, or publication bias.

## Scientific frameworks and further reading

The skill currently uses PICO/PECO, PRISMA-style flow reporting, RoB2, ROBINS-I,
Newcastle-Ottawa, AMSTAR-2, and GRADE. Additional frameworks under consideration are tracked in
[`SCIENTIFIC_FRAMEWORKS_OPTIMIZATION_PLAN.md`](SCIENTIFIC_FRAMEWORKS_OPTIMIZATION_PLAN.md).

| Framework | Purpose | Reference |
|---|---|---|
| PICO | Intervention-focused clinical question framing | [NLM: Using PICO to frame clinical questions](https://www.nlm.nih.gov/oet/ed/pubmed/pubmed_in_ebp/02-100.html) |
| PECO | Exposure, etiology, environmental health, and risk-factor question framing | [Morgan et al. 2018, *Environment International*](https://pmc.ncbi.nlm.nih.gov/articles/PMC6908441/) |
| PEO | Population-exposure-outcome framing where no explicit comparator is needed | [City St George's, University of London: PICO and PEO frameworks](https://libguides.city.ac.uk/SHS-Litsearchguide/frameworks) |
| PCC | Scoping review question framing: Population, Concept, Context | [JBI Scoping Review Network resources](https://jbi.global/scoping-review-network/resources) |
| SPIDER | Qualitative and mixed-methods search framing | [Cooke, Smith & Booth 2012](https://doi.org/10.1177/1049732312452938) |
| SPICE | Service delivery and implementation questions: Setting, Perspective, Intervention, Comparison, Evaluation | [NCCMT registry: SPICE framework](https://www.nccmt.ca/knowledge-repositories/search/326) |
| TIDieR | Intervention description and replication detail | [Hoffmann et al. 2014, *BMJ*](https://www.bmj.com/content/348/bmj.g1687) |
| RoB 2 | Risk of bias in randomized trials | [Cochrane RoB 2](https://methods.cochrane.org/risk-bias-2) |
| ROBINS-I | Risk of bias in non-randomized studies of interventions | [Cochrane ROBINS-I](https://methods.cochrane.org/robins-i) |
| Newcastle-Ottawa Scale | Cohort and case-control study appraisal | [Ottawa Hospital Research Institute: NOS](https://www.ohri.ca/programs/clinical_epidemiology/oxford.asp) |
| AMSTAR-2 | Appraisal of systematic reviews | [AMSTAR website](https://amstar.ca/Amstar-2.php) |
| QUADAS | Diagnostic accuracy risk of bias and applicability | [University of Bristol QUADAS resources](https://www.bristol.ac.uk/population-health-sciences/projects/quadas/) |
| PROBAST | Prediction model risk of bias and applicability | [Moons et al. 2019, *Annals of Internal Medicine*](https://doi.org/10.7326/M18-1377) |
| CASP qualitative checklist | Qualitative study appraisal | [CASP checklists](https://casp-uk.net/casp-tools-checklists/) |
| GRADE | Certainty of evidence by outcome | [GRADE Working Group](https://www.gradeworkinggroup.org/) |
| PRISMA 2020 | Systematic review reporting | [PRISMA statement](https://www.prisma-statement.org/) |
| SWiM | Reporting synthesis without meta-analysis | [Campbell et al. 2020, *BMJ*](https://www.bmj.com/content/368/bmj.l6890) |
| EQUATOR Network | Reporting guideline library, including CONSORT, STROBE, SPIRIT, STARD, TRIPOD, COREQ, ENTREQ, and extensions | [EQUATOR Network](https://www.equator-network.org/) |

## Where things live

**Wiki mode:**

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

**Standalone repo mode** (no wiki, no wiki-manager — `references/pool-architecture.md` for the
full layout):

```
<repo>/
  data/sources/    assets/ (hash-named PDFs), sources/ + events.jsonl (evidence-kernel snapshots)
  data/papers/     registry.jsonl (canonical), pool.jsonl (regenerated view), extractions/, appraisals/<project>/
  projects/<slug>/ protocol.md  synthesis.md  manuscript.qmd  refs.bib  figures/  tables/
  runs/<slug>/     the run — same shape as the wiki-mode run directory above
  exports/         wiki / bib / html / docx / pdf
```

Run `python3 scripts/research.py init <path>` once per repo, then `research.py project create
<slug> --repo <path>` per manuscript project.

## Outputs

| Output | Command | Good for |
|---|---|---|
| `outputs/report.md` | always | The primary deliverable: evidence, conflicts, certainty, gaps, hypotheses, citations |
| `outputs/digest.md` | Stage 7b | Short summary used as the body of `<wiki>/research/reviews/<slug>.md` |
| `outputs/report.html` | `html_report.py build` | Self-contained shareable page |
| `outputs/prisma.{json,md}` | `corpus.py prisma --out` | Flow numbers: identified → deduped → screened → included |
| PDF / docx | `render.py all --formats pdf,docx` | Citable document via Quarto |
| `outputs/verification.json` | `verify.py run` | Audit: citations resolve, records complete, hypotheses not phrased as findings |
| `<wiki>/research/` concepts | `okf.py promote --run-dir <dir> --wiki <root>` | Durable wiki knowledge (wiki mode only — no repo-mode equivalent) |
| `<wiki>/assets/papers/pool.jsonl` | `pool.py sync --run-dir <dir>` (automatic at end of Stage 5/6) | Cross-run extraction/appraisal reuse, wiki mode |
| `<repo>/data/papers/registry.jsonl` | `registry.py promote` / `appraise-promote --run-dir <dir>` (automatic at end of Stage 5/6) | Cross-run extraction/appraisal reuse, repo mode |
| Wiki-wide `refs.bib` | `pool.py bib --wiki <root> --out <path>` | BibTeX for every paper ever pooled, any run, for publications |
| Project `refs.bib` | `research.py project create` scaffolds it; fill via `render.py`/hand-edit | Repo-mode manuscript citations, kept alongside `manuscript.qmd` |

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
   `library.py ingest-inbox --run-dir <dir>` directly (`--no-apply` previews matches without
   rewriting `corpus.jsonl`).
4. Matched PDFs are filed into `<wiki>/assets/papers/` (sha256 dedupe); the run then extracts,
   appraises and re-synthesizes the newly available studies, and the report notes which studies
   arrived by manual supply. Unmatched PDFs stay in `inbox/`, listed with a reason.

Full policy — block format, abstract-only vs. true quarantine, matching thresholds, the
step-by-step resume loop — is in `references/acquisition.md` §5–6.

## Shared paper pool — never extract the same paper twice

**Wiki mode:** `<wiki>/assets/papers/pool.jsonl` is a second, wiki-wide store next to the PDF
library (`index.json`): one record per paper (keyed by the same `pmid`/`doi`/`pmcid`
`evidence_id` used everywhere), carrying full bibliographic metadata plus a *pointer* to
whichever run's `workspace/extractions/` and `workspace/appraisals/` files actually hold that
paper's structured extraction/appraisal. The heavy content stays inside the run that produced
it — only the pointer and the biblio are shared.

**Standalone repo mode:** `<repo>/data/papers/registry.jsonl` plays the same role, but holds
extractions/appraisals directly rather than pointing at them: `registry.py promote` /
`appraise-promote` copy a run's finished work into `data/papers/extractions/` /
`data/papers/appraisals/<project>/` once and for all, so later reuse never depends on the
producing run's directory still existing. `pool.jsonl` here is a regenerated *view* of the
registry, not a second source of truth — never hand-edit it. Appraisal is scoped **per project**
(`--project <slug>`): a paper appraised for one manuscript's question is not automatically
appraised for another.

Everything below applies to both, with `--wiki <root>` swapped for `--repo <path>`:

- **Automatic.** Every run calls `pool.py sync` (wiki) or `registry.py promote`/
  `appraise-promote` (repo) when Stage 5 (and again Stage 6) finishes — no separate step to
  remember.
- **Seed before external search.** At the start of Stage 2, run
  `python3 scripts/pool.py seed --run-dir <dir> --wiki <root>` (or `--repo <path>`) to add
  matching pooled papers to the new run's `corpus.jsonl` with `source: pool` and
  `first_seen_query: pool-seed`. They are candidates only: Stage 3 still screens them against
  the new question before retrieval or reuse.
- **Reuse.** Before dispatching an extraction or appraisal subagent, the run calls
  `pool.py reuse --run-dir <dir> --wiki <root> --pmid <pmid>` (or `--repo <path> [--project
  <slug>]`); a hit copies the other run's result in. In wiki mode it also re-registers the
  snapshot(s) its spans cite into this run's own evidence-kernel store (content-addressed by
  `sha256(url+text)`, so the copy is byte-identical and gets the same `source_id`) — the reused
  quotes verify locally, not just for display. In repo mode no re-registration step is needed:
  Stage 4/5 already write snapshots straight into the repo's global source store, so any run
  constructed against the same `repo_root` already resolves them. Either way, a paper researched
  once is never re-extracted by a later run on a different question, and its citations stay
  fully auditable.
- **BibTeX.** `python3 scripts/pool.py bib --wiki <root> --out refs.bib` emits one consolidated
  `.bib` covering every paper ever pooled in that wiki — not just one run's included set — ready
  to cite in a manuscript. `--select appraised` narrows it to papers that also have an appraisal.
  (Repo mode: build a project's `refs.bib` from its own `manuscript.qmd` citations via
  `render.py`, or hand-maintain it — there is no repo-wide equivalent of `pool.py bib` yet.)
- **Build a pool without appraising.** Set `pipeline.stop_after_stage: 5` (Profiles, above) to
  run search/screen/retrieve/extract only; the pool still fills in, appraisal and synthesis just
  don't run. Good for surveying a topic's extractable literature before committing to a full
  systematic review.
- **Freshness stays honest**: a reused snapshot is logged as a `register` event, never `fresh`
  — the text wasn't retrieved in this run. `verify.py`'s C-FRESH-FETCH reports it as non-fresh
  accordingly, same as any other cached text; the integrity/span checks (C-SNAPSHOT, C-SPAN)
  pass normally since the content is now locally present and hash-verified.
- **Bulk intake, repo mode only.** `registry.py add --pmid/--doi` (single paper via PubMed
  lookup), `add-pdf --file <pdf>` (sha256-deduped asset store), `import-bib --file refs.bib`
  (bulk from an existing `.bib`), `import-folder --dir <pdfs>` (bulk from a folder of PDFs) —
  all converge on the same registry, so a large reference set can be staged cheaply before
  deciding what to extract.
- **Moving a wiki pool into a repo, or back.** `pool.py migrate --from-wiki <wiki> --repo <path>`
  copies an existing wiki's pool — papers, extractions, appraisals, and the snapshots their
  spans cite — into a new standalone repo, reporting any pointer that no longer resolves rather
  than dropping it silently. `research.py export wiki --repo <path> --wiki <wiki> --project
  <slug>` goes the other way: a one-way courtesy copy of the repo's pool plus one project's
  bundle into a wiki, not a migration.

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
| `library.py` | Shared PDF library at `<wiki>/assets/papers/` (wiki mode): `init`, `lookup`, `add`, `ingest-inbox`, `list` |
| `pool.py` | Shared extraction/appraisal pool, `--wiki` or `--repo`: `seed`, `lookup`, `reuse` (carries spans across runs); `sync`/`bib`/`list` are wiki-mode only; `migrate --from-wiki` bridges a wiki pool into a repo |
| `research.py` | Standalone repo (repo mode) lifecycle: `init`, `project create`/`list`, `export wiki` adapter |
| `registry.py` | `data/papers/registry.jsonl` canonical registry (repo mode's counterpart to `library.py`+`pool.py`): `add`/`add-pdf`/`import-bib`/`import-folder`, `lookup`/`list`/`pool`, `promote`/`appraise-promote` (verified by default — `--strict`/`--no-verify`), `bib` (repo-mode BibTeX export) |
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
