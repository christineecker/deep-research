# deep-research — design & implementation plan

Status: approved design, not yet built.
Date: 2026-09-08
Inspiration: ApodexAI/FrontierAgent (coordinator + parallel bounded sub-agents, sandboxed
inputs/workspace/outputs, resumable checkpoints, transparent deliverables).

Purpose: thorough PubMed-centred literature research — gather evidence for a scientific
question, critically appraise it, synthesise it, and surface gaps and new hypotheses.

---

## 1. Decisions (from planning Q&A)

| Topic | Decision |
|---|---|
| Sources | User-selectable per run: narrow / medium / wide / max (see §3) |
| Rigor | User-selectable per run: fast / standard / systematic |
| Defaults | Apodex-style profiles. Default one-shot profile is `standard / medium / protocol+strategy gate / report.md`; prompt only for missing high-impact choices |
| Gates | User-selectable per run: protocol+strategy / screening / both / none |
| Parallelism | Yes — subagent fan-out for search, screening, extraction, appraisal |
| Outputs | `report.md` always; optional HTML artifact, Quarto `.qmd`→PDF/docx, OKF wiki promotion |
| Wiki | Chosen per run at **config time** (needed early, see §5). Missing wiki → confirm creation + location |
| PDF library | `<wiki-root>/assets/papers/` — shared cross-run cache, rung 0 of the ladder |
| Full text | Mandatory goal, not abstracts. Full ladder incl. PDF extraction (§5) |
| Paywalled | OA ladder first; then drive the user's logged-in Chrome (chrome-devtools MCP) with per-run confirmation. **No paywall circumvention.** |
| Unobtainable | Quarantine + alert user, who drops PDFs into `inbox/`; rerun resumes |
| Filters | Years, authors, journals, article types, species/age, language, OA-only (§4) |
| Wiki format | OKF v0.2-compatible markdown bundle. PubMed-derived concepts include full bibliographic metadata in frontmatter |
| Runtime model | Apodex-style run directory with inputs/workspace/outputs, trace, task board, checkpoints, logs, and trajectories |

---

## 2. Environment review (verified 2026-09-08)

| Check | Result | Consequence for build |
|---|---|---|
| `python` | MISSING; only `python3` 3.14.7 | Shebang `#!/usr/bin/env python3`. Global "prefer python" rule cannot apply |
| Python deps | `requests` OK, `pdfminer` OK; `lxml`, `bs4`, `fitz`, `habanero`, `rispy` MISSING | Parse XML with stdlib `xml.etree`; PDF text via `pdftotext` binary, `pdfminer` fallback. **Zero pip installs.** |
| Binaries | `pdftotext`, `pandoc`, `quarto`, `tesseract`, `jq`, `curl` all present; `ocrmypdf` MISSING | Quarto export path viable. OCR = `pdftotext`; if <100 chars extracted, per-page `tesseract` |
| NCBI API key | none in env | Throttle E-utilities to 3 req/s; honor optional `NCBI_API_KEY` for 10 req/s |
| PubMed MCP | native `date_from`/`date_to`/`datetype`, `sort`, `retstart` pagination | Timeframe filter needs no script. But MCP exposes no hit counts / no query translation → E-utilities script still required for reproducible strategy logs |
| Wikis on disk | 9 dirs, incl. `grant-wiki` and `w3-wiki` not listed in CLAUDE.md | Enumerate the wikis dir at runtime; never hardcode the list |
| `knowledge-wiki` | git repo + Obsidian vault; already has `assets/ inbox/ raw/ outputs/ wiki/` | PDF library goes to `assets/papers/` to avoid colliding with existing images |

### Open flag
Wikis are git repos on iCloud. Plan assumes `assets/papers/*.pdf` is added to the wiki's
`.gitignore` (PDFs stay local) while `assets/papers/index.json` IS committed, so the library
manifest is versioned. Revisit if the user prefers committing PDFs.

---

## 3. Scope tiers

- **narrow** — PubMed MCP only (`search_articles`, `get_article_metadata`, `find_related_articles`)
- **medium** — + NCBI E-utilities script (exact boolean/MeSH, hit counts, large result sets)
- **wide** — + Europe PMC, bioRxiv/medRxiv preprints (preprints tagged loudly)
- **max** — + web: guidelines, grey literature, Scholar Gateway / Consensus connectors
  (both currently unauthorized — skill must detect and tell the user to authorize in
  claude.ai connector settings rather than failing silently)

## 4. Filters → E-utilities field tags

| Filter | Example | Tag |
|---|---|---|
| Years | 2000–2026 | `("2000"[dp] : "2026"[dp])` |
| Author | Kaufmann J | `Kaufmann J[au]`; `[1au]` / `[lastau]` for first/last author |
| Journal | JAMA Psychiatry | `[ta]` |
| Article type | RCT, meta-analysis, guideline | `[pt]` |
| Species / age | human; child 6–12 | `[mh]` + age filters |
| Language | English, German | `[la]` |
| Free full text | OA only | `[sb]` |
| Sample size, funding, setting | — | applied at screening, not in the query |

---

## 5. Pipeline

Stage 0 — **configuration**: load profile/config, infer sane defaults, then ask only for
missing high-impact choices: scope, rigor, gates, outputs, filters, target wiki, and any
authorized-browser use.

| # | Stage | Runs in |
|---|---|---|
| 1 | Protocol: PICO/PECO, inclusion/exclusion, limits → `protocol.md` | main |
| 2 | Search: 4–8 orthogonal queries (MeSH + free-text + citation chaining); every query and hit count logged | subagents, 1/query |
| 3 | Screen: dedupe (PMID/DOI/normalized title) → title/abstract triage, include/exclude + reason; retraction check | subagents, batched |
| 4 | Retrieve: full-text acquisition ladder (below) | subagents |
| 5 | Extract: design, N, population, I/C, outcomes, effect + CI, funding/COI, limitations → `corpus.jsonl` | subagents, 1/paper |
| 6 | Appraise: RoB2 / ROBINS-I / Newcastle-Ottawa / AMSTAR-2 by design; then GRADE domains | subagents |
| 7 | Synthesize: direction, agreement/conflict + why, certainty, gaps, labelled hypotheses | main only |
| 8 | Verify/report: citation audit, corpus consistency, unsupported-claim check, OKF validation | main or fast reporter |

Subagents return structured JSON, never raw paper text → main context stays small over long runs.
State is on disk at task granularity; a rerun resumes incomplete/failed tasks rather than
restarting the last whole stage.

### Profiles

- **fast** — `scope=narrow`, `rigor=fast`, `gates=none`, bounded article count, report only.
- **standard** — `scope=medium`, `rigor=standard`, `gates=protocol+strategy`, report + OKF wiki when a wiki is selected. This is the default one-shot profile.
- **systematic** — `scope=wide`, `rigor=systematic`, `gates=both`, PRISMA-style screening log, full verifier pass, report + OKF + optional Quarto export.
- **max** — `scope=max`, `rigor=systematic`, `gates=both`, includes guidelines/grey literature/preprints with source-type tagging and connector authorization checks.

Profiles are overridable by `runs/<slug>/config.json`. Resume never re-prompts for values
already present in config unless the user explicitly asks to change them.

### Task board and resumability

Every unit of work is recorded in `taskboard.jsonl`:

```json
{
  "task_id": "extract:pmid:12345678",
  "stage": "extract",
  "status": "pending|active|completed|blocked|failed|cancelled",
  "inputs_hash": "sha256:...",
  "attempts": 1,
  "worker": "extractor-03",
  "output_path": "workspace/extractions/pmid-12345678.json",
  "error": null,
  "created_at": "2026-09-08T00:00:00Z",
  "updated_at": "2026-09-08T00:00:00Z"
}
```

The coordinator updates task state before and after each subagent assignment. Completed task
outputs are immutable unless their `inputs_hash` changes. Failed tasks record diagnostics and
can be retried independently.

### Execution guardrails

- `max_subagents`, `max_parallel`, `max_wall_time`, `max_articles`, and
  `max_fulltext_failures` are profile-controlled and written to `config.json`.
- Duplicate PubMed/E-utilities/web queries are detected before execution and skipped or merged
  into the existing task result.
- Repeated identical tool calls or repeated task assignment with no new completed/blocked work
  triggers a no-progress guard; the coordinator writes diagnostics to `engine.log` and moves to
  verification with a provisional report when possible.
- Subagents cannot spawn further subagents. They receive bounded task inputs and return
  structured JSON only.

### Failure semantics

- Acquisition failure: quarantine the source, continue the run, and mark synthesis provisional.
- Malformed subagent JSON: retry once with the schema error; then mark the task failed/blocked.
- Reporter/export failure: preserve `outputs/report.md` and record the failure in `engine.log`.
- Sandbox, authorization, or paywall-policy failure: fail closed; never fall back to unisolated
  host access or paywall circumvention.
- OKF validation failure: keep the report, block wiki promotion, and write validation errors to
  `outputs/okf-validation.md`.

### Full-text acquisition ladder (stage 4)
Each paper walks the rungs until text is in hand. Rung used is recorded as `source_tier` +
`access_route` in `corpus.jsonl`.

0. `<wiki>/assets/papers/` local library (index.json lookup by DOI/PMID/fuzzy title)
1. PMC OA — `get_full_text_article` (PubMed MCP)
2. Europe PMC — `convert_article_ids` → PMCID → `fullTextXML` REST
3. Unpaywall — DOI → OA location (email as required API param)
4. Publisher HTML — DOI resolve → defuddle/firecrawl scrape
5. OA PDF download → `pdftotext -layout`; <100 chars → per-page `tesseract` OCR
6. Logged-in Chrome via chrome-devtools MCP for subscribed journals — **per-run confirmation
   before driving the browser**; saves PDF into the library
7. Preprint twin (bioRxiv/medRxiv/SSRN), tagged as preprint — content differs from the
   published version, so flagged loudly
8. Quarantine → `runs/<slug>/missing.md` with PMID, DOI, title, journal, and direct links

Run never stalls on quarantine: it continues, marks the synthesis provisional, lists the gap.
**Resume loop**: user drops PDFs into `runs/<slug>/inbox/`; rerun matches each PDF to its
quarantined record (PDF metadata DOI, else first-page text), extracts, appraises, re-synthesises,
and files the PDF into the library. Report notes which studies arrived by manual supply.

---

## 6. Invariants (enforced in SKILL.md)

- No PubMed-backed claim without a PMID that was actually retrieved this run
- No claim from any source without a retrieved `evidence_id`: PMID, DOI, PMCID, preprint DOI, guideline URL, report URL, or bundle-relative OKF concept path
- Abstract-only vs full-text tagged on every extraction; abstract-only never appraised as if full
- Null/negative findings actively searched, not just whatever surfaced
- Conflicts surfaced with explanation, never averaged away
- "New insights / hypotheses" strictly separated from "what the evidence shows"
- Retracted / Expression-of-Concern papers flagged at screening
- PubMed MCP attribution requirement honored (cite PubMed + DOIs)
- No paywall circumvention of any kind
- OKF concept documents use `type` plus recommended `title`, `description`, `resource`, `tags`, `generated`, `sources`, `verified`, `status`, and `stale_after` where applicable
- PubMed-derived OKF concepts MUST preserve PubMed bibliographic metadata in frontmatter: `pmid`, `doi`, `pmcid`, `authors`, `journal`, `publication_date`, `article_types`, `mesh_terms`, `keywords`, `publication_status`, `retraction_status`, citation details, and source URLs when available
- Per-claim attribution uses markdown footnotes keyed to `sources[].id`; do not rely on a body-only citations list
- Root wiki `index.md` declares `okf_version: "0.2"` when deep-research creates or controls the bundle
- Standard markdown links are the graph layer; Obsidian wikilinks may be additive only

---

## 6a. OKF PubMed metadata

Every PubMed-derived concept written into the wiki is an OKF v0.2 concept. `type` is the only OKF-required field, but deep-research treats the following bibliographic fields as required when PubMed supplies them:

```yaml
---
type: Study
title: Example Trial Title
description: Randomized controlled trial of ...
resource: https://pubmed.ncbi.nlm.nih.gov/12345678/
tags: [deep-research, study, rct]
generated: { by: deep-research/0.1, at: 2026-09-08T00:00:00Z }
verified: { by: process:deep-research-verifier, at: 2026-09-08T00:00:00Z }
status: stable
pmid: "12345678"
doi: "10.1000/example"
pmcid: "PMC1234567"
authors:
  - family: Smith
    given: Jane A
    initials: JA
    affiliation: Department of Example Medicine, Example University
    collective: null
journal:
  title: Journal of Example Medicine
  iso_abbrev: J Example Med
  issn: "1234-5678"
publication_date: 2024-06-15
epub_date: 2024-05-20
volume: "12"
issue: "3"
pages: "101-115"
abstract: Structured abstract text when available from PubMed.
article_types: [Randomized Controlled Trial]
mesh_terms: [Depression, Cognitive Behavioral Therapy]
keywords: [adolescents, remission]
grants:
  - id: R01-EXAMPLE
    agency: National Institutes of Health
publication_status: ppublish
retraction_status: none
sources:
  - id: pubmed-12345678
    resource: https://pubmed.ncbi.nlm.nih.gov/12345678/
    title: PubMed record
  - id: doi-10-1000-example
    resource: https://doi.org/10.1000/example
    title: DOI landing page
  - id: fulltext-12345678
    resource: /references/papers/pmid-12345678.pdf
    title: Local full-text PDF
---
```

Unknown values are omitted or set to `null` consistently by `scripts/okf.py`; never invent bibliographic fields. Author order, affiliations, pagination, publication dates, article types, MeSH terms, grants, DOI/PMCID/PMID, and retraction metadata are preserved from PubMed metadata when present.

### OKF concept taxonomy and paths

Deep-research may define domain concept types, but consumers must tolerate unknown types per
OKF. Stable wiki concepts use durable paths, not run-specific paths:

```text
<wiki-root>/
  index.md                  # MAY include okf_version: "0.2"
  log.md
  reviews/<slug>.md         # type: Review
  protocols/<slug>.md       # type: Protocol
  searches/<slug>.md        # type: Search Strategy
  studies/pmid-<pmid>.md    # type: Study
  studies/doi-<slug>.md     # type: Study when no PMID exists
  claims/<slug>.md          # type: Evidence Claim
  outcomes/<slug>.md        # type: Outcome
  populations/<slug>.md     # type: Population
  interventions/<slug>.md   # type: Intervention
  comparators/<slug>.md     # type: Comparator
  appraisals/<slug>.md      # type: Appraisal
  gaps/<slug>.md            # type: Evidence Gap
  hypotheses/<slug>.md      # type: Hypothesis
  source-documents/<slug>.md # type: Source Document
  references/papers/
    index.json
```

Run artifacts may link to durable concepts, but promoted wiki concepts do not live under
`runs/<slug>/`. Each directory gets an `index.md` when generated by `scripts/okf.py`, and
updates append to the nearest `log.md` with ISO `YYYY-MM-DD` headings.

## 7. File layout

```
deep-research/
  SKILL.md                 # router: config protocol, pipeline, invariants, delegation
  PLAN.md                  # this file
  README.md
  references/
    schema.md              # JSON contracts for subagent returns
    search-strategy.md     # MeSH, hedges, filter→tag table, orthogonal query design
    acquisition.md         # the ladder, rung by rung; chrome rung rules
    appraisal.md           # RoB2, ROBINS-I, NOS, AMSTAR-2, GRADE
    synthesis.md           # effect direction, heterogeneity, conflict handling
    reporting.md           # PRISMA flow, citation format
  scripts/
    eutils.py              # esearch/efetch/elink, throttle, retry, hit counts
    fulltext.py            # acquisition ladder, resumable
    library.py             # assets/papers/index.json, matching, inbox ingestion
    corpus.py              # corpus.jsonl, dedupe, PRISMA counters, task checkpoints
    okf.py                 # OKF v0.2 concept writer + validator, PubMed metadata frontmatter
    render.py              # md → qmd + bib → quarto render (pdf/docx)
    verify.py              # citation/corpus/OKF consistency checks
    eval.py                # deterministic smoke/evaluation harness
  templates/
    protocol.md  evidence-table.md  report.md  report.qmd  refs.bib
  runs/<slug>/
    session.json           # resumable conversation/run checkpoint
    config.json            # profile, limits, filters, wiki target, gates
    trace.jsonl            # ordered LLM/tool/subagent events
    engine.log             # warnings, failures, diagnostics
    taskboard.jsonl        # task-level state machine
    trajectories/          # coordinator and subagent reports
    inputs/                # read-only user-supplied files and manual PDFs
    workspace/             # protocol/search/screen/extract/appraise scratch state
    outputs/               # report.md, report.qmd, validation reports, HTML
    missing.md             # quarantined papers needing user-supplied access
    inbox/                 # backward-compatible manual PDF drop target
```

---

## 8. Build order (6 phases, each independently testable)

**Phase 1 — skeleton + contracts**
- `SKILL.md`: frontmatter (triggers: "literature review", "systematic review",
  "what does the evidence say", "pubmed", "deep research"), stage-0 config protocol,
  profiles, pipeline table, invariants, delegation rules, execution guardrails, and failure
  semantics. Target <=500 lines; detail in references.
- `references/schema.md`: JSON contracts subagents must return (screening verdict,
  extraction record, appraisal record, taskboard record, verifier result). The spine —
  everything conforms to it.
- `templates/*`.

**Phase 2 — acquisition (load-bearing)**
- `scripts/eutils.py` — esearch returns count + translated query + PMIDs; efetch XML →
  normalized JSON; elink. 3 req/s throttle, backoff, `NCBI_API_KEY` honored. stdlib + requests.
- `scripts/fulltext.py` — the ladder, resumable, records `source_tier`/`access_route`.
- `scripts/library.py` — index.json, DOI+PMID+fuzzy-title match, sha256 dedupe, inbox ingestion.
- `references/acquisition.md`.

**Phase 3 — search & screening**
- `references/search-strategy.md` — MeSH vs free-text, filter→tag table, validated design
  hedges (Cochrane RCT filter, SIGN SR filter), citation chaining, building genuinely
  orthogonal queries rather than 8 near-duplicates.
- `scripts/corpus.py` — corpus.jsonl, dedupe, PRISMA counters, task-level checkpointing.
- Screening subagent prompt block in SKILL.md: criteria in,
  `{pmid, decision, reason, criterion_failed}` out.

**Phase 4 — appraisal & synthesis**
- `references/appraisal.md` — which tool for which design; how to phrase "unclear" honestly.
- `references/synthesis.md` — effect-direction tabulation, heterogeneity, publication-bias
  signals, the hard wall between evidence and hypothesis.
- Retraction check wired into screening.

**Phase 5 — output**
- `scripts/render.py` — report.md → report.qmd + refs.bib (BibTeX from corpus.jsonl) →
  `quarto render` to PDF/docx.
- `scripts/okf.py` — writes OKF v0.2 concepts into the chosen wiki, including
  PubMed metadata frontmatter, `sources`, standard markdown links, `index.md`,
  and `log.md`. Obsidian conventions may be additive only.
- `scripts/verify.py` — final reporter/verifier pass: checks every citation maps to
  `corpus.jsonl`/OKF `sources`, every included study has screening/extraction/appraisal records,
  abstract-only claims are labelled, missing full texts are listed, and hypotheses are not
  phrased as established evidence.
- HTML artifact — evidence table, effect-direction chart, per-study cards, gaps shown honestly.
- `README.md` — usage, config options, inbox resume loop.

**Phase 6 — evaluation harness**
- `scripts/eval.py` deterministic smoke tests:
  known PubMed query with expected PMIDs; duplicate DOI/title merge; PMC full-text success;
  paywalled article quarantine; inbox PDF resume; malformed subagent JSON retry/block;
  report citation verifier; OKF v0.2 validator; interrupted run resumes by task.
- Start with concurrency 1. Treat total possible model parallelism as evaluation concurrency
  multiplied by `max_parallel`.

**Then**: end-to-end dry run on a real question at `fast` rigor; verify the quarantine →
inbox → resume loop, taskboard resume, verifier pass, and OKF promotion actually work; fix what breaks.
