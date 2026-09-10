# reporting.md — reporting manual

Stage 8 of `SKILL.md` "Pipeline". Runs in **main**. Produces `outputs/report.md` (always), optionally
`report.qmd` → PDF/docx via `scripts/render.py`, an HTML artifact, and the OKF bundle promotion
via `scripts/okf.py`. Verified by `scripts/verify.py` → `outputs/verification.json`
(schema §9) before delivery.

Governing invariants (`SKILL.md` "Invariants"):

- No claim without an `evidence_id` retrieved this run.
- Per-claim attribution uses markdown footnotes keyed to `sources[].id`. A body-only citation
  list is **not** acceptable.
- PubMed MCP attribution requirement honored: cite PubMed **and** DOIs.
- Abstract-only, preprint, retracted, and quarantined status stated at the point of use.
- Hypotheses never phrased as established evidence.

---

## 1. PRISMA 2020 flow

Reported at every rigor level. At `fast` the numbers are still counted; only the dual-screening
row is absent.

### Counters

Normative names emitted by `scripts/corpus.py prisma` (JSON object; also rendered as the flow
diagram in the report). Names are snake_case and map one-to-one onto PRISMA 2020 boxes.

| Counter | PRISMA 2020 box | Derived from |
|---|---|---|
| `records_identified_databases` | Records identified from: Databases | retrievals with `source` in {`pubmed`, `europepmc`} |
| `records_identified_registers` | Records identified from: Registers | register searches; `0` when none run |
| `records_identified_other` | Records identified from: Websites / Organisations / Citation searching | `source` in {`web`, `guideline`} and elink citation chaining |
| `records_removed_duplicates` | Duplicate records removed | `corpus.py` dedupe on PMID / DOI / normalized title |
| `records_removed_automation` | Records marked ineligible by automation tools | filter-stage removals applied by script, not by a screener |
| `records_removed_other` | Records removed for other reasons | e.g. malformed metadata; each reason enumerated |
| `records_screened` | Records screened | corpus records reaching stage 3 |
| `records_excluded` | Records excluded | `screening.decision == "exclude"` at title/abstract |
| `reports_sought` | Reports sought for retrieval | records entering stage 4 |
| `reports_not_retrieved` | Reports not retrieved | `fulltext.status == "missing"` → `missing.md` |
| `reports_assessed` | Reports assessed for eligibility | records with `fulltext.status` in {`fulltext`, `abstract_only`} reaching full-text assessment |
| `reports_excluded` | Reports excluded, with reasons | object `{criterion_id: count}` keyed by `criterion_failed` from the protocol |
| `studies_included` | Studies included in review | distinct studies after merging multiple reports of one study |
| `reports_included` | Reports of included studies | corpus records with `screening.decision == "include"` |

Additional deep-research counters, reported alongside the flow (not PRISMA boxes):

| Counter | Meaning |
|---|---|
| `included_fulltext` | included records with `fulltext.status == "fulltext"` |
| `included_abstract_only` | included records with `fulltext.status == "abstract_only"` |
| `included_preprints` | included records with `is_preprint == true` |
| `retracted_flagged` | records with `retraction_status != "none"`, broken down by status |
| `quarantined` | `fulltext.status == "missing"`; equals `reports_not_retrieved` and the `missing.md` row count |
| `manual_inbox_supplied` | records with `fulltext.access_route == "inbox_manual"` |

Invariants the verifier enforces:

```text
records_screened  = sum(records_identified_*) - sum(records_removed_*)
records_excluded + reports_sought = records_screened
reports_assessed  = reports_sought - reports_not_retrieved
reports_included <= reports_assessed
quarantined       = reports_not_retrieved = rows in missing.md
```

### Dual screening agreement

Reported only when dual screening ran (`systematic`, `max` — `SKILL.md`).

| Reported value | Definition |
|---|---|
| `dual_screened_records` | records screened independently by `screener-a` and `screener-b` |
| `disagreements` | count of `adjudication record`s written |
| `agreement_rate` | `1 - (disagreements / dual_screened_records)`, to 3 significant figures |
| `adjudicated_to_include` / `adjudicated_to_exclude` / `adjudicated_to_unclear` | `final_decision` breakdown |

Report raw agreement plus counts. Do **not** report a kappa unless it was actually computed from
the full contingency table; if it is computed, name the coefficient (Cohen's kappa) and give the
table. Never present raw agreement as kappa.

```text
GOOD "Two screeners independently assessed 412 records; they disagreed on 37
      (raw agreement 91.0%). All 37 were adjudicated by a third worker; 12 were
      adjudicated to include."
BAD  "Inter-rater reliability was good."
```

---

## 2. Citation format

### Per-claim footnotes

Every claim-bearing sentence carries a markdown footnote whose key is a `sources[].id` from the
OKF concept for that evidence (`references/okf-bundle.md`). Source-id shapes: `pubmed-<pmid>`,
`doi-<slugified-doi>`, `pmc-<pmcid>`, `fulltext-<pmid>`, `url-<slug>`.

```markdown
Group CBT reduced depressive symptoms at 12 weeks relative to waitlist
(SMD -0.41, 95% CI -0.68 to -0.14).[^pubmed-12345678]

[^pubmed-12345678]: Smith JA, Doe R, Muller K. Cognitive behavioral therapy for
    adolescent depression: a randomized trial. *J Example Med.* 2024;12(3):101-115.
    PMID [12345678](https://pubmed.ncbi.nlm.nih.gov/12345678/).
    DOI [10.1000/example](https://doi.org/10.1000/example).
    PMCID [PMC1234567](https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/).
    RoB 2: some concerns. Full text.
```

| # | Rule |
|---|---|
| R1 | One footnote per cited evidence item per claim. A claim resting on three studies carries three footnote references, not one covering footnote. |
| R2 | The footnote key **is** the `sources[].id`. It must resolve in `corpus.jsonl` / the OKF concept's `sources[]`. Verifier check `C-CITE-RESOLVE`. |
| R3 | A trailing "References" list is a convenience rendering of the footnotes, never a substitute. A report whose body cites nothing and whose end lists everything fails. |
| R4 | Every included study is cited at least once, or appears in `verification.json.uncited_citations` with the reason stated in the report (usually: excluded from effect synthesis for critical risk of bias). |
| R5 | Never cite a record that was not retrieved this run. Background statements that cannot be cited from the corpus are cut, not softened. |
| R6 | Footnotes carry the appraisal verdict and the evidence basis inline (as above). This is what makes abstract-only labelling survive copy-paste. |

### PubMed MCP attribution

Any evidence obtained through the PubMed MCP or NCBI E-utilities carries **both** the PubMed
citation and the DOI where a DOI exists. Satisfying only one violates the attribution requirement
(`SKILL.md` "Invariants").

- Methods states: "Records were identified via PubMed / NCBI E-utilities and the PubMed MCP
  connector", plus the date of the last search.
- PMC full text obtained through the connector adds the PMCID link.
- Europe PMC-sourced records name Europe PMC; web-sourced records name the URL and access date.

### Identifier rendering

| Identifier | Rendering | Link |
|---|---|---|
| PMID | `PMID 12345678` (string; never an integer) | `https://pubmed.ncbi.nlm.nih.gov/<pmid>/` |
| DOI | `10.1000/example`, lowercase, no `https://doi.org/` prefix in the text | `https://doi.org/<doi>` |
| PMCID | `PMC1234567` | `https://pmc.ncbi.nlm.nih.gov/articles/<pmcid>/` |
| Preprint DOI | DOI form, plus server name (bioRxiv / medRxiv / Research Square) | DOI link |
| Local PDF | not shown in the report body; recorded as `fulltext-<pmid>` in `sources[]` with a wiki-root-relative `resource` | — |
| Guideline / web | organisation, title, access date | canonical URL |

Missing identifiers are omitted — never invented, never written as `N/A` (schema rule S3).
Standard markdown links are the graph layer; Obsidian wikilinks are additive only (`SKILL.md` "Invariants").

---

## 3. Report skeleton

Each section names its data source. `scripts/verify.py` checks section presence by heading-regex
match (`REQUIRED_SECTIONS`), not by number or exact order — renumbering or adding subsections is
safe as long as the required headings still match their pattern.

| § | Section | Data source | Notes |
|---|---|---|---|
| 0 | Title block | `config.json` | question, profile, scope, rigor, run slug, date of last search, **PROVISIONAL** marker when applicable |
| 1 | Short answer and plain-language summary | synthesis | short answer first, then the <=200 word summary; GRADE wordings only; every sentence traceable to §8 |
| 2 | Evidence at a glance | synthesis + appraisal `grade` | one row per outcome: studies/participants, direction, best estimate, certainty, main limitation — summarizes only what §8/§9 fully support; never a number not stated there |
| 3 | Question and protocol | `protocol.md` | PICO/PECO, inclusion/exclusion criteria with ids (`I1`, `E2`, …), pre-declared timepoint bands, moderators, MIDs, protocol deviation table (deviation, reason, affected records/outcomes, likely impact) |
| 4 | Methods | `workspace/search/*.json`, `taskboard.jsonl`, `config.json` | 4.1 search (every `query_string`, `translated_query`, `source`, `count`, `executed_at`, verbatim and reproducible; unauthorized/unreachable connectors and what that means for coverage), 4.2 screening (dual-screening design), 4.3 retrieval (acquisition-ladder rungs used), 4.4 extraction (models per stage), 4.5 appraisal (tools by design; GRADE itself is reported in §9) |
| 5 | PRISMA flow + screening log | `corpus.py prisma` | §1 (reporting.md) counters, exclusion reasons by criterion id, dual-screening agreement |
| 6 | Characteristics of included studies | `corpus.jsonl` + `workspace/extractions/*.json` | evidence table: study, design, N, population, I/C, outcomes, funding/COI, evidence basis, source tier |
| 7 | Risk of bias and appraisal | `workspace/appraisals/*.json` | per study, design, tool named, key domain concerns, overall judgement, evidence basis; `tool: "none"` records listed with the reason no in-scope tool applies |
| 8 | Results by outcome | synthesis (`references/synthesis.md` §2) | one subsection per synthesis unit: effect-direction table, prose reading, conflicts with attribution, heterogeneity; required harms/adverse-events subsection (or a stated reason harms were not assessed); funding/COI synthesis subsection feeding §9's publication-bias column |
| 9 | Certainty of evidence | appraisal `grade` + synthesis | GRADE summary-of-findings-style table per outcome, with the reason for every downgrade/upgrade |
| 10 | Conflicts and inconsistencies | synthesis §5 | each conflict, its attribution (C1–C9), or an explicit "unexplained" |
| 11 | Evidence gaps | corpus + synthesis | "no study reported X" statements only; observations, not explanations |
| 12 | Limitations of this review | `engine.log`, `verification.json`, `missing.md` | quarantine, abstract-only reliance, scope tier, language/database restrictions, no pooling |
| 13 | **New insights / hypotheses** | synthesis §7 | hard-walled section, placed after limitations; opportunity map table, then every hypothesis item labelled and paired with what would test it |
| 14 | Unobtainable / quarantined evidence | `missing.md` | PMID, DOI, PMCID, title, journal, links, rung reached, resume instructions |
| 15 | References | footnote definitions | rendering of the in-body footnotes (format defined in "2. Citation format" above); never the sole citation mechanism |
| 16 | Provenance | `config.json`, `verification.json` | profile, budgets, models per stage, script versions, verification summary, OKF promotion status, direct links to protocol.md, evidence-table.md, prisma.json/md, missing.md, verification.json, result.json, and the OKF bundle path |

`templates/report.md` carries this skeleton; `templates/evidence-table.md` carries §6.
`templates/report.qmd` mirrors the same section set and order (Quarto heading levels instead of
numbered `##`/`###`).

---

## 4. Honest statement patterns

### Abstract-only evidence

Label at the point of use, in the footnote, and in the evidence table. Three places, every time.

```text
GOOD "One trial reports a 24-week remission benefit; this rests on the abstract only,
      as the full text could not be obtained, and its risk of bias was not
      assessable.[^pubmed-23456789]"
BAD  "One trial reports a 24-week remission benefit.[^pubmed-23456789]"
      (basis hidden; C-FULLTEXT fails on abstract_only_claims[].labelled == false)
```

### Quarantined / unobtainable papers

```text
GOOD "Three records could not be obtained through the open-access ladder (PMID 11111111,
      22222222, 33333333; all reached rung 8). They are listed in section 14 with direct
      links. The synthesis is PROVISIONAL pending their retrieval; PDFs dropped into the
      run's inbox/ are ingested on rerun."
BAD  "A small number of papers were unavailable."
BAD  omitting them entirely.
```

Never imply a paywalled paper was assessed. Never paraphrase its abstract as if it were the study.

### Preprints — tagged loudly

`is_preprint == true` records carry the tag in the body sentence, in the footnote, in the evidence
table, and in the plain-language summary if they contribute to it.

```text
GOOD "**Preprint (not peer reviewed).** A medRxiv preprint reports a larger effect in
      primary care.[^doi-10-1101-2024-01-01-24300000]"
BAD  "A recent study reports a larger effect in primary care."
```

A rung-6 preprint twin of a published article is flagged as such: its content differs from the
published version, and the report states which version was read.

### Retracted / Expression of Concern

Flagged at screening (`SKILL.md` "Invariants"; `screening verdict.retraction_flag`).

| `retraction_status` | Handling |
|---|---|
| `retracted` | Excluded from synthesis. Listed in §12 with the retraction notice. If cited at all, the citation carries **RETRACTED** in the sentence and in the footnote |
| `expression_of_concern` | May be included; carries **Expression of Concern** at every point of use; contributes to GRADE risk of bias |
| `corrected` | Included; the report states that a correction exists and which version was extracted |
| `none` | Normal handling |

```text
GOOD "**RETRACTED.** An earlier trial reported a large benefit; it was retracted in 2023
      and is excluded from the synthesis.[^pubmed-44444444]"
BAD  citing a retracted trial with no marker anywhere.
```

### Provisional syntheses

**PROVISIONAL** appears in the title block whenever a trigger in `references/synthesis.md` §8
fires. The report states what would lift it (which records, which route) and repeats the caveat
locally in each affected outcome subsection, not only globally.

---

## 5. Verifier reporting checks

`scripts/verify.py` writes `outputs/verification.json` (schema §9). Any `status: "fail"` makes the
report provisional and blocks OKF promotion. The report is still delivered.

| `check_id` | Passes when |
|---|---|
| `C-CITE-RESOLVE` | Every footnote key in `report.md` resolves to a `sources[].id` / `evidence_id` in `corpus.jsonl`, and every cited item was retrieved this run |
| `C-CORPUS-COMPLETE` | Every included record has a screening verdict, an extraction record, and an appraisal record (or a recorded reason it has none) |
| `C-SEARCH-LOG` | Every executed query has a `search result record` with `hit_count_logged == true`, and all appear verbatim in §3 |
| `C-RETRACTION` | Every record with `retraction_status != "none"` is flagged at every point of use; retracted records are excluded from synthesis |
| `C-FULLTEXT` | `missing_fulltext` matches `missing.md`; every entry of `abstract_only_claims` has `labelled == true` |
| `C-HYPOTHESIS-WALL` | No sentence in §13 uses evidence-side verb forms; no sentence outside §13 asserts an unlabelled hypothesis; `unsupported_claims` is empty |
| `C-OKF` | Bundle concepts validate against `references/okf-bundle.md`; PubMed metadata preserved; `sources[]` present. `skipped` when no wiki promotion was requested |

Reporting-stage checks defined here, added to schema §9's open `check_id` list:

| `check_id` | Passes when |
|---|---|
| `C-PRISMA` | The §1 counter invariants hold and the flow's arithmetic closes |
| `C-PREPRINT` | Every `is_preprint == true` record is tagged at every point of use |
| `C-ATTRIBUTION` | Every PubMed-sourced footnote carries a PubMed link, plus the DOI where a DOI exists |
| `C-PROVISIONAL` | If any provisional trigger fired, **PROVISIONAL** appears in the title block and §14 lists the quarantined records |
| `C-SECTIONS` | Every skeleton section in §3 is present (empty-with-a-stated-reason is acceptable; missing is not) |

### Pre-delivery gate

```text
1. corpus.py prisma   -> counters; invariants close
2. verify.py          -> outputs/verification.json
3. any fail?          -> mark PROVISIONAL, block OKF promotion, keep report.md,
                         write outputs/okf-validation.md if the OKF check failed
4. any warn?          -> the warning is stated in section 12, not buried
5. deliver report.md; render / HTML / OKF promotion only after 3-4
```

`outputs/report.md` is preserved even when rendering, export, or OKF promotion fails; the failure
is recorded in `engine.log` (`SKILL.md` "Failure semantics").
