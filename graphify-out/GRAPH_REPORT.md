# Graph Report - .  (2026-09-09)

## Corpus Check
- 3 files · ~125,192 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1330 nodes · 3067 edges · 56 communities (52 shown, 4 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 159 edges (avg confidence: 0.73)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Corpus & PRISMA Bookkeeping|Corpus & PRISMA Bookkeeping]]
- [[_COMMUNITY_Verifier CLI|Verifier CLI]]
- [[_COMMUNITY_Assembler Gate|Assembler Gate]]
- [[_COMMUNITY_Report Rendering & BibTeX|Report Rendering & BibTeX]]
- [[_COMMUNITY_Run Watch Dashboard|Run Watch Dashboard]]
- [[_COMMUNITY_PubMed E-utilities Client|PubMed E-utilities Client]]
- [[_COMMUNITY_PDF Library Ingest|PDF Library Ingest]]
- [[_COMMUNITY_OKF Bundle Builder|OKF Bundle Builder]]
- [[_COMMUNITY_OKF Concepts & Shadow Wiki|OKF Concepts & Shadow Wiki]]
- [[_COMMUNITY_Snapshot Store Core|Snapshot Store Core]]
- [[_COMMUNITY_Full-Text Acquisition Driver|Full-Text Acquisition Driver]]
- [[_COMMUNITY_Source Fetch & Spans CLI|Source Fetch & Spans CLI]]
- [[_COMMUNITY_Test Helpers & Assembler Tests|Test Helpers & Assembler Tests]]
- [[_COMMUNITY_OKF Validation Rules|OKF Validation Rules]]
- [[_COMMUNITY_Publisher Integrity Preflight|Publisher Integrity Preflight]]
- [[_COMMUNITY_HTML Report Sections|HTML Report Sections]]
- [[_COMMUNITY_Study Record View Model|Study Record View Model]]
- [[_COMMUNITY_HTTP Client & Europe PMC|HTTP Client & Europe PMC]]
- [[_COMMUNITY_HTML Report Rows & PRISMA|HTML Report Rows & PRISMA]]
- [[_COMMUNITY_Store Read Facade|Store Read Facade]]
- [[_COMMUNITY_Record Schema Contracts|Record Schema Contracts]]
- [[_COMMUNITY_Minimal YAML Parser|Minimal YAML Parser]]
- [[_COMMUNITY_Concurrency Invariant Tests|Concurrency Invariant Tests]]
- [[_COMMUNITY_Freshness & Event Log|Freshness & Event Log]]
- [[_COMMUNITY_Evidence Kernel Gate Rendering|Evidence Kernel Gate Rendering]]
- [[_COMMUNITY_Evidence-Kernel Doctrine|Evidence-Kernel Doctrine]]
- [[_COMMUNITY_Acquisition Rungs & Registration|Acquisition Rungs & Registration]]
- [[_COMMUNITY_Store Error Taxonomy|Store Error Taxonomy]]
- [[_COMMUNITY_Search Strategy & Subagent Rules|Search Strategy & Subagent Rules]]
- [[_COMMUNITY_Report Build & Record Loading|Report Build & Record Loading]]
- [[_COMMUNITY_Span-Backed Quote Derivation|Span-Backed Quote Derivation]]
- [[_COMMUNITY_Reporting Contracts & Templates|Reporting Contracts & Templates]]
- [[_COMMUNITY_Run Profiles & Invariants|Run Profiles & Invariants]]
- [[_COMMUNITY_HTML Truncation Detection|HTML Truncation Detection]]
- [[_COMMUNITY_Span Slicing & Verification|Span Slicing & Verification]]
- [[_COMMUNITY_OKF Bundle Boundaries|OKF Bundle Boundaries]]
- [[_COMMUNITY_Taskboard & Event Schema|Taskboard & Event Schema]]
- [[_COMMUNITY_Offline Fixture Eval Harness|Offline Fixture Eval Harness]]
- [[_COMMUNITY_Synthesis Without Meta-Analysis|Synthesis Without Meta-Analysis]]
- [[_COMMUNITY_Full-Text vs Abstract-Only Basis|Full-Text vs Abstract-Only Basis]]
- [[_COMMUNITY_Verifier Checks & Reason Codes|Verifier Checks & Reason Codes]]
- [[_COMMUNITY_Identity Hashes & Immutability|Identity Hashes & Immutability]]
- [[_COMMUNITY_HTML-to-Text Fallback|HTML-to-Text Fallback]]
- [[_COMMUNITY_Acquisition Ladder Doctrine|Acquisition Ladder Doctrine]]
- [[_COMMUNITY_Appraisal Tools & Honest Unclear|Appraisal Tools & Honest Unclear]]
- [[_COMMUNITY_Shared Schema Rules|Shared Schema Rules]]
- [[_COMMUNITY_Store Contract Tests|Store Contract Tests]]
- [[_COMMUNITY_Shared Script Helpers|Shared Script Helpers]]
- [[_COMMUNITY_Run Directory & Stage Pointer|Run Directory & Stage Pointer]]
- [[_COMMUNITY_Fulltext CLI Entrypoint|Fulltext CLI Entrypoint]]
- [[_COMMUNITY_Library CLI Entrypoint|Library CLI Entrypoint]]
- [[_COMMUNITY_Paywall Fail-Closed Policy|Paywall Fail-Closed Policy]]
- [[_COMMUNITY_Eight-Stage Pipeline|Eight-Stage Pipeline]]
- [[_COMMUNITY_Library Parser & CLI|Library Parser & CLI]]
- [[_COMMUNITY_Paywall Ethics & Constraints|Paywall Ethics & Constraints]]
- [[_COMMUNITY_Pipeline Definition|Pipeline Definition]]

## God Nodes (most connected - your core abstractions)
1. `deep-research skill` - 36 edges
2. `Study` - 33 edges
3. `Verifier` - 32 edges
4. `Store` - 31 edges
5. `Validator` - 27 edges
6. `StoreError` - 27 edges
7. `build()` - 26 edges
8. `RunReader` - 26 edges
9. `E()` - 25 edges
10. `cmd_promote()` - 24 edges

## Surprising Connections (you probably didn't know these)
- `Orthogonal Query Design (four axes)` --semantically_similar_to--> `Health Alerts (degraded runs must be announced)`  [INFERRED] [semantically similar]
  references/search-strategy.md → SKILL.md
- `Paywalled Cohort PDF Fixture` --conceptually_related_to--> `snapshot record`  [INFERRED]
  tests/fixtures/pdf/paywalled-cohort.pdf → references/schema/10-snapshot.md
- `Paywalled Cohort PDF Fixture` --semantically_similar_to--> `Paywalled HTML Fixture`  [INFERRED] [semantically similar]
  tests/fixtures/pdf/paywalled-cohort.pdf → tests/fixtures/html/paywalled.html
- `No Pip Installs Constraint` --rationale_for--> `OKF 0.2 Bundle Frontmatter Spec`  [INFERRED]
  README.md → references/okf-bundle.md
- `Fresh-Fetch Rule` --semantically_similar_to--> `Non-Negotiable Invariants`  [INFERRED] [semantically similar]
  references/evidence-kernel.md → SKILL.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Critical appraisal framework** — readme_rob2, readme_robins_i, readme_newcastle_ottawa, readme_amstar_2, readme_grade [INFERRED]
- **Python script system** — readme_eutils_py, readme_fulltext_py, readme_library_py, readme_corpus_py, readme_okf_py, readme_render_py, readme_html_report_py, readme_verify_py, readme_status_py [INFERRED]
- **Run data storage** — readme_config_json, readme_taskboard_jsonl, readme_corpus_jsonl, readme_report_md [INFERRED]

## Communities (56 total, 4 thin omitted)

### Community 0 - "Corpus & PRISMA Bookkeeping"
Cohesion: 0.05
Nodes (99): Path, Parse one JSON file. Raises whatever `json` raises; callers own their error poli, read_json(), add_inputs(), add_run_dir(), advisory_lock(), append_line(), atomic_write() (+91 more)

### Community 1 - "Verifier CLI"
Cohesion: 0.05
Nodes (39): atomic_write(), build_parser(), cmd_run(), doi_slug(), FatalError, main(), markdown_summary(), norm_ws() (+31 more)

### Community 2 - "Assembler Gate"
Cohesion: 0.05
Nodes (51): _alnum(), appraisal_artifact(), Artifact, Assembler, build_parser(), cmd_run(), Corpus, _evidence_identifiers() (+43 more)

### Community 3 - "Report Rendering & BibTeX"
Cohesion: 0.06
Nodes (68): Exception, alias_map(), atomic_write(), bib_author_name(), bib_authors(), bib_escape(), bib_key(), bib_title() (+60 more)

### Community 4 - "Run Watch Dashboard"
Cohesion: 0.07
Nodes (34): active_workers(), alerts_of(), bar(), budgets_of(), build_parser(), clip(), fmt_dur(), funnel_of() (+26 more)

### Community 5 - "PubMed E-utilities Client"
Cohesion: 0.08
Nodes (53): Any, Element, _abstract(), _authors(), _authors_clause(), build_parser(), build_query(), _chunks() (+45 more)

### Community 6 - "PDF Library Ingest"
Cohesion: 0.09
Nodes (44): cmd_add(), cmd_ingest_inbox(), cmd_init(), cmd_list(), cmd_lookup(), doi_from_pdf(), entry_is_preprint(), ingest_inbox() (+36 more)

### Community 7 - "OKF Bundle Builder"
Cohesion: 0.09
Nodes (49): base_front(), build_biblio(), build_parser(), build_run_concepts(), bundle_root_front(), citation_line(), cmd_selftest(), collect_sources() (+41 more)

### Community 8 - "OKF Concepts & Shadow Wiki"
Cohesion: 0.11
Nodes (28): append_log(), build_shadow_wiki(), cmd_init(), cmd_promote(), cmd_validate(), Concept, Fence, FenceError (+20 more)

### Community 9 - "Snapshot Store Core"
Cohesion: 0.07
Nodes (39): Abstract-only evidence labeling, AMSTAR-2, config.json, Consensus connector, corpus.jsonl, corpus.py script, deep-research skill, Europe PMC (+31 more)

### Community 10 - "Full-Text Acquisition Driver"
Cohesion: 0.17
Nodes (29): ISO-8601 UTC with a literal `Z`, per schema rule S2 (`references/schema/00-share, utcnow(), acquire_record(), already_satisfied(), cmd_acquire(), cmd_resolve_mcp(), cmd_status(), Ctx (+21 more)

### Community 11 - "Source Fetch & Spans CLI"
Cohesion: 0.11
Nodes (31): AssetHashMismatchError, build_parser(), _check_offsets(), check_snapshot_integrity(), compute_content_hash(), compute_source_id(), ExcerptMismatchError, list_snapshots() (+23 more)

### Community 12 - "Test Helpers & Assembler Tests"
Cohesion: 0.12
Nodes (28): acquire(), build_parser(), check_url_policy(), cmd_fetch(), cmd_local(), cmd_spans(), _decode(), _expand() (+20 more)

### Community 13 - "OKF Validation Rules"
Cohesion: 0.12
Nodes (11): make_run(), minimal_corpus_record(), CompletedProcess, Path, run_py(), write_json(), write_jsonl(), AssemblerArchitectureTest (+3 more)

### Community 15 - "HTML Report Sections"
Cohesion: 0.15
Nodes (10): _evidence_identifier(), IntegrityFinding, Preflight, `pmid:12345678` -> ('pmid', '12345678'). Non-literature ids yield (None, None)., Publisher-side integrity checks over one run directory (Phase 5).      Instantia, Gate-controlled: blocking only when the evidence-kernel gate is on., User-supplied PDF bytes must still hash to the recorded asset digest., `result.sources[]` is derived from snapshots; any drift is tampering. (+2 more)

### Community 16 - "Study Record View Model"
Cohesion: 0.14
Nodes (23): datetime, append_event(), ensure_run(), _next_event_id(), _ordered(), parse_ts(), A record violated the schema (bad enum, wrong type, missing field)., Parse an ISO-8601 Z timestamp (S2). Returns None when unparseable. (+15 more)

### Community 17 - "HTTP Client & Europe PMC"
Cohesion: 0.14
Nodes (19): _asset_digest_ok(), cmd_write(), events_path(), fresh_event(), freshness(), has_fresh_retrieval(), Path, Lowercase hex sha256 of a file's bytes. No prefix. (+11 more)

### Community 18 - "HTML Report Rows & PRISMA"
Cohesion: 0.13
Nodes (14): _emit(), cmd_read(), cmd_events(), cmd_fresh(), cmd_stats(), cmd_verify(), iter_snapshots(), Run-level tallies: snapshots (with integrity), events by type, fresh sources. (+6 more)

### Community 19 - "Store Read Facade"
Cohesion: 0.21
Nodes (19): A(), E(), glyph(), group_summary(), h_card(), h_chart_section(), h_evidence_section(), h_insights_section() (+11 more)

### Community 20 - "Record Schema Contracts"
Cohesion: 0.10
Nodes (4): A corpus record joined to its extraction and appraisal records., evidence_basis, preferring the extraction record, else the corpus status., Resolution R8: `missing` before any attempt is not `unobtainable`., Study

### Community 21 - "Minimal YAML Parser"
Cohesion: 0.16
Nodes (16): RuntimeError, alert_acquisition_health(), _epmc_fulltext(), _epmc_search(), Http, HttpError, jats_to_text(), OfflineError (+8 more)

### Community 22 - "Concurrency Invariant Tests"
Cohesion: 0.17
Nodes (19): build_parser(), build_rows(), effect_text(), fmt_num(), h_prisma_section(), main(), make_row(), null_value_for() (+11 more)

### Community 23 - "Freshness & Event Log"
Cohesion: 0.18
Nodes (19): evidence_id Primary Key (S9), Unknown-Is-Null Discipline (S3), corpus record, screening verdict, Unclear Decision Must Not Be Coerced, adjudication record, Screener Disagreement Rate Logging, extraction record (+11 more)

### Community 24 - "Evidence Kernel Gate Rendering"
Cohesion: 0.19
Nodes (19): cmd_write(), ensure_bundle(), _is_seq_item(), load_document(), OkfError, _parse_map(), _parse_node(), _parse_scalar() (+11 more)

### Community 25 - "Evidence-Kernel Doctrine"
Cohesion: 0.13
Nodes (9): _Args, _event(), EventLogUnderConcurrencyTest, HostThrottleUnderConcurrencyTest, Concurrency invariants for `fulltext.py acquire --workers`.  Two things break si, The per-host interval stays a floor when workers share one `Http`., A structurally valid §11 event for `snapshot`. `fresh` is false (R22)., R23 holds when many threads register snapshots at once. (+1 more)

### Community 26 - "Acquisition Rungs & Registration"
Cohesion: 0.15
Nodes (12): compute_provisional(), h_cards_section(), h_gaps_section(), h_kernel_block(), Kernel, kernel_state_html(), num(), Included studies with no span-backed excerpt at all (schema R16, `unverified`). (+4 more)

### Community 27 - "Store Error Taxonomy"
Cohesion: 0.17
Nodes (16): deep-research Operator Manual, deep-research Coordinator Skill, Appraisal Subagent Prompt, Spans Required for Every Non-Unclear Judgement, quotes[] Is Derived, Never Written, Evidence Is an Offset, Not a Sentence You Typed, Extraction Subagent Prompt, null Is Not 'none declared' (+8 more)

### Community 28 - "Search Strategy & Subagent Rules"
Cohesion: 0.16
Nodes (15): Ctx, canonical_url(), `access` for a rung result (schema.md §10 enum, R21).      A truncation-detected, `origin` for a rung result (schema.md §10 enum).      A rung-0 library hit inher, Stable identifier URL for a snapshot whose rung recorded no retrieval URL., Fold acquired text into the run's snapshot store. Returns the `source_id` or Non, register_acquisition(), rung4_unpaywall() (+7 more)

### Community 29 - "Report Build & Record Loading"
Cohesion: 0.26
Nodes (14): Path, format_corpus_table(), format_stage_status(), format_summary(), get_current_stage(), main(), progress_bar(), Format stage progress line. (+6 more)

### Community 30 - "Span-Backed Quote Derivation"
Cohesion: 0.23
Nodes (14): build(), escape_json_for_script(), extract_report_hypotheses(), h_banner(), h_run_section(), load_dir_records(), Path, Serialise for embedding inside a `<script type="application/json">` element. (+6 more)

### Community 31 - "Reporting Contracts & Templates"
Cohesion: 0.17
Nodes (13): hit_count_logged / C-SEARCH-LOG Gate, Evidence-Kernel Checks (C-SNAPSHOT, C-SPAN, C-FRESH-FETCH, C-ASSEMBLER), OKF Promotion Fail-Closed Gate, verifier result, Span Rules P1-P6, unverified Backward Compatibility (R16), assembler result (result.json), diagnostics.unresolved[] Entry (+5 more)

### Community 32 - "Run Profiles & Invariants"
Cohesion: 0.19
Nodes (11): derived_quotes(), h_quotes(), is_span_backed(), quotes_from_accepted(), The three evidence states: verified excerpt, unverified legacy record, or absent, True when a `quotes[]` entry carries the §7 DERIVED triple (source_id, start, en, Partition a record's `quotes[]` into (span-backed, span-less legacy) entries., The derived `quotes[]` of one `accepted[]` artifact (schema §13).      `scripts/ (+3 more)

### Community 33 - "HTML Truncation Detection"
Cohesion: 0.17
Nodes (12): Connector Preflight, Health Alerts (degraded runs must be announced), Non-Negotiable Invariants, Run Profiles (fast/standard/systematic/max), Quarantine → Inbox → Resume Loop, Scope Tiers (narrow/medium/wide/max), PDF Library Git Policy, Library Matching Thresholds (sha256/DOI/PMID/PMCID/fuzzy title) (+4 more)

### Community 34 - "Span Slicing & Verification"
Cohesion: 0.18
Nodes (7): HTMLParser, detect_truncation(), html_to_text(), stdlib HTML -> text. lxml/bs4 are NOT installed (`SKILL.md` "Scripts")., Return (truncated, reasons). `references/acquisition.md` rung 5 / schema.md §4., rung5_oa_fetch(), _TextHTML

### Community 35 - "OKF Bundle Boundaries"
Cohesion: 0.22
Nodes (11): Wiki Boundary (research/ owned, wiki/ never written), Publisher Integrity Preflight (okf.py promote --check), Bundle Boundary Rules B1-B5, Markdown Links as the Graph Layer, log.md Append Convention, Per-Claim Footnote Attribution, PubMed Bibliographic Frontmatter Block, OKF 0.2 Bundle Frontmatter Spec (+3 more)

### Community 36 - "Taskboard & Event Schema"
Cohesion: 0.18
Nodes (11): Malformed-JSON Single Retry (S5), Schema Shared Rules (S1-S10), Subagent Text Boundary (S4), receipt Record, Task Status Transition Machine, task_id Grammar, taskboard record, search result record (+3 more)

### Community 37 - "Offline Fixture Eval Harness"
Cohesion: 0.47
Nodes (10): main(), make_run(), Path, run_case(), run_cli(), source_for_mode(), write_extraction(), write_json() (+2 more)

### Community 38 - "Synthesis Without Meta-Analysis"
Cohesion: 0.22
Nodes (10): Not a Meta-Analysis (scope boundary), AMSTAR-2 (systematic reviews), GRADE Certainty Rating, Conflict Taxonomy C1-C9, Effect-Direction Tabulation, Three Kinds of Heterogeneity, No Pooled Estimates (hard constraint), Publication-Bias Signals Without Meta-Analysis (+2 more)

### Community 39 - "Full-Text vs Abstract-Only Basis"
Cohesion: 0.22
Nodes (10): Offline Fixture Testing (eval.py, DEEP_RESEARCH_FIXTURES), Citation Chaining (elink), E-utilities Field Tags and Filters, Query Execution and Logging Contract (L1-L7), Search Strategy Manual, Why MeSH Alone Is Always Wrong, Orthogonal Query Design (four axes), Hit-Set Overlap Report (overlap.py) (+2 more)

### Community 40 - "Verifier Checks & Reason Codes"
Cohesion: 0.22
Nodes (9): Bounded-Subagent Delegation Rules, Subagent Shell Hygiene (never ls), Adjudicator Subagent Prompt, Screening Decision Rules, Screening Subagent Prompt, Shared Subagent Output Directory, Report Raw Agreement, Never Kappa-by-Assertion, Validated Design Hedges (Cochrane HSSS, SIGN) (+1 more)

### Community 41 - "Identity Hashes & Immutability"
Cohesion: 0.22
Nodes (9): Completed-Record Immutability (S10), Run-Relative Path Rule (S7), inputs_hash Invalidation Key, quotes[] Derived Field, Snapshot Immutability and Integrity, snapshot record, Source Identity Hash (source_id / content_hash), Character-Offset Semantics (R10) (+1 more)

### Community 42 - "HTML-to-Text Fallback"
Cohesion: 0.28
Nodes (7): cmd_read(), Structured integrity result for one snapshot; never raises for a bad snapshot., `text[start:end]` with R10/P1/P2 enforced. Offsets are Python `str` indices., The excerpt for a span: verified snapshot, in range, `end` exclusive, ≤2000 char, slice_span(), slice_text(), verify_snapshot()

### Community 43 - "Acquisition Ladder Doctrine"
Cohesion: 0.29
Nodes (8): Paywalled HTML Fixture, fulltext Sub-Object, Acquisition Ladder source_tier 0-7, Truncation Detection Invariant, evidence_basis (fulltext vs abstract_only), C-FULLTEXT Abstract-Only Labelling Check, Snapshot access Enum, R21 access vs evidence_basis vs fulltext.status Mapping

### Community 44 - "Appraisal Tools & Honest Unclear"
Cohesion: 0.29
Nodes (3): html_to_text(), Minimal, dependency-free HTML-to-text: drops script/style, keeps block breaks., _TextHTML

### Community 45 - "Shared Schema Rules"
Cohesion: 0.29
Nodes (7): No Pip Installs Constraint, Full-Text Acquisition Ladder (rungs 0-7), Rung 1 PubMed MCP Coordinator Handoff, OCR Fallback (pdftotext → pdfminer → tesseract), Acquisition Resumability, HTML Truncation Detector, Abstract-Only Restricted Appraisal

### Community 46 - "Store Contract Tests"
Cohesion: 0.33
Nodes (7): GRADE Certainty Wording (verbatim shapes), PRISMA 2020 Flow Counters, Report Skeleton (16 sections), Verifier Reporting Checks (C-*), The Hard Wall (evidence vs hypotheses), report.md Template, report.qmd Quarto Template

### Community 47 - "Shared Script Helpers"
Cohesion: 0.33
Nodes (7): Saying Unclear Honestly (rules U1-U4), Newcastle-Ottawa Scale, RoB 2 (randomized trials), ROBINS-I (non-randomized studies), Target-Trial Framing, Appraisal Tool Selection by Design, Honest Statement Patterns

### Community 49 - "Fulltext CLI Entrypoint"
Cohesion: 0.33
Nodes (6): Paywalled Cohort PDF Fixture, Event Types (fetch / local_pdf / register / read), Fresh-Fetch Rule, User-Supplied-PDF Freshness Exception, accepted[] Artifact Entry, Assembler Derivation Rule

### Community 50 - "Library CLI Entrypoint"
Cohesion: 0.33
Nodes (5): emit_json(), Shared helpers for the deep-research scripts.  Imported as a sibling module, mat, Print a payload as pretty JSON and return an exit code. (`source.py`, `store.py`, ASCII-fold to a lowercase hyphenated slug. Concept slugs (`okf.py`, `verify.py`), slugify()

### Community 51 - "Paywall Fail-Closed Policy"
Cohesion: 0.50
Nodes (4): No-Progress Guard, Run Directory Layout, Stage Pointer, Task Board (taskboard.jsonl)

### Community 52 - "Eight-Stage Pipeline"
Cohesion: 0.67
Nodes (3): build_parser(), main(), ArgumentParser

### Community 53 - "Library Parser & CLI"
Cohesion: 0.67
Nodes (3): build_parser(), main(), ArgumentParser

## Knowledge Gaps
- **47 isolated node(s):** `Path`, `Namespace`, `ArgumentParser`, `CompletedProcess`, `ArgumentParser` (+42 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `datetime` connect `Study Record View Model` to `Corpus & PRISMA Bookkeeping`, `Report Rendering & BibTeX`, `Run Watch Dashboard`, `PubMed E-utilities Client`, `PDF Library Ingest`, `OKF Bundle Builder`, `Source Fetch & Spans CLI`, `Library CLI Entrypoint`, `Concurrency Invariant Tests`, `Report Build & Record Loading`?**
  _High betweenness centrality (0.362) - this node is a cross-community bridge._
- **Why does `StoreError` connect `Source Fetch & Spans CLI` to `Assembler Gate`, `Report Rendering & BibTeX`, `Test Helpers & Assembler Tests`, `Appraisal Tools & Honest Unclear`, `Study Record View Model`, `HTTP Client & Europe PMC`?**
  _High betweenness centrality (0.076) - this node is a cross-community bridge._
- **Why does `read_json()` connect `Corpus & PRISMA Bookkeeping` to `Verifier CLI`, `OKF Concepts & Shadow Wiki`, `HTML Report Sections`, `Library CLI Entrypoint`, `Evidence Kernel Gate Rendering`, `Span-Backed Quote Derivation`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **What connects `Path`, `Shared helpers for the deep-research scripts.  Imported as a sibling module, mat`, `ISO-8601 UTC with a literal `Z`, per schema rule S2 (`references/schema/00-share` to the rest of the system?**
  _331 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Corpus & PRISMA Bookkeeping` be split into smaller, more focused modules?**
  _Cohesion score 0.05324967824967825 - nodes in this community are weakly interconnected._
- **Should `Verifier CLI` be split into smaller, more focused modules?**
  _Cohesion score 0.052407614781634936 - nodes in this community are weakly interconnected._
- **Should `Assembler Gate` be split into smaller, more focused modules?**
  _Cohesion score 0.05257312106627175 - nodes in this community are weakly interconnected._