# evidence-kernel.md — the evidence-proof layer

The kernel that makes claims checkable: immutable source snapshots, character-offset spans, an
append-only retrieval log, an assembler gate, and a publisher integrity preflight.

Contracts: `references/schema.md` §10 (`snapshot record`), §11 (`event record`), §12
(`claim span record`), §13 (`assembler result`), §9 (verifier checks `C-SNAPSHOT`, `C-SPAN`,
`C-FRESH-FETCH`, `C-ASSEMBLER`), resolutions R10–R24. Design source:
`VALIDATION_ARCHITECTURE_PLAN.md`. Scripts (built later, against those contracts):
`scripts/source.py`, `scripts/store.py`, `scripts/assemble.py`.

This layer does not replace the PubMed pipeline. `eutils.py`, `fulltext.py` and `library.py` keep
doing acquisition; whenever they obtain usable text they **register** it into the snapshot store,
and from that moment on it is addressable by offset.

---

## 1. Why

An agent writing "the trial reported a 41% reduction (Smith 2024, p. 7)" has produced a sentence,
not a proof. The quotation marks are free. The page number is free. Both survive review because
nobody re-opens the PDF.

The kernel's single premise:

> **Agent-written text is never itself evidence.**

An agent may say *what* it concluded and *where* it read it. It may not supply the reading. Every
excerpt that reaches a report, an evidence table or a wiki concept is re-sliced from an immutable
snapshot by a script, twice — once at assembly, once at publish. The three things an agent
contributes to the evidence record are a `claim` label, a `source_id`, and a `start`/`end` pair.
Everything else — URL, title, access level, PMID/DOI/PMCID, excerpt text, section, page — is
derived from the snapshot.

This turns four failure modes from "hopefully caught in review" into "mechanically rejected":

| Failure | Caught by |
|---|---|
| Fabricated or subtly re-worded quote | excerpt re-slice mismatch (`EXCERPT_MISMATCH`) |
| Invented source | unknown `source_id` (`UNKNOWN_SOURCE`) |
| Stale evidence recycled from an old run or a wiki note | fresh-fetch rule (`NO_FRESH_FETCH`) |
| Tampered snapshot | hash recomputation (`SNAPSHOT_HASH_MISMATCH`) |

---

## 2. The snapshot store

`<run>/sources/src-<sha256>.json`, one file per snapshot, written by `scripts/source.py` on top
of `scripts/store.py`.

**Guarantees.**

1. **Write-once.** Snapshots are created with `O_EXCL`. There is no update path. A correction is
   a new snapshot with a new id; the old one stays (R12).
2. **Content-addressed identity.**
   `source_id = "src-" + sha256_hex(utf8(url) + NUL + utf8(text))`, where NUL is one literal
   `0x00` byte. Same URL and same text ⇒ same id ⇒ same file ⇒ a re-fetch of unchanged text is a
   no-op. Different text ⇒ different id, always (R11).
3. **Independent body hash.** `content_hash = "sha256:" + sha256_hex(utf8(text))`, over the text
   alone, so a body edit is detectable without reference to the URL.
4. **Verified on every read.** `source.py read`, `source.py spans`, `assemble.py`, `verify.py`
   and `okf.py promote` all recompute both digests from the file's own `url` and `text` and
   compare. A mismatch is a hard error, never a warning: the snapshot is tampered, no span
   resolves against it, `C-SNAPSHOT` fails, promotion is blocked.
5. **Stable offsets.** `text` is stored decoded-UTF-8 and never normalized — no NFC/NFD pass, no
   whitespace collapsing, no newline rewriting, on write or on read. Offsets under an existing
   claim therefore never drift (R10).
6. **No PDF duplication.** PDFs stay in the shared library `<wiki>/assets/papers/`. The snapshot
   records `asset = {path, sha256, bytes}` with a **wiki-root-relative** path — a named exception
   to schema rule S7. There is no `<run>/assets/` (R13).

**Access and origin.** `access` (`full_text` | `abstract` | `preprint` | `guideline` | `web`)
says what the text can support; `origin` (`pubmed` | `pmc` | `europepmc` | `unpaywall` | `oa-pdf`
| `user-supplied-pdf` | `web`) says where it came from. Both are closed enums. `access` maps onto
the existing `evidence_basis` / `fulltext.status` vocabulary per R21; where snapshot and record
disagree, the snapshot wins and the artifact is rejected rather than corrected.

**Source identity vs evidence identity.** One study (`evidence_id`, schema rule S9) may have
several snapshots: a PubMed abstract, a PMC full text, a user-supplied PDF. The relation is
many-to-one. A span must name a snapshot registered for its own `evidence_id`, and the snapshot's
`paper` triple must agree with that `evidence_id` (R14).

---

## 3. The event log and the fresh-fetch rule

`<run>/events.jsonl`, append-only. Four event types: `fetch` (a real retrieval performed in this
run), `local_pdf` (a hash-checked user-supplied PDF), `register` (text obtained elsewhere in the
pipeline, folded into the store without a new retrieval), `read` (a bounded window read by a
subagent — audit only).

> **The rule:** a `supported` verdict counts only if every source backing the claim has a fresh
> retrieval logged in *this* run.

A source is fresh iff `events.jsonl` holds a line with that `source_id`, `type` of `fetch` or
`local_pdf`, `fresh: true`, `at` at or after the run's `created_at`, and a `sha256` that still
matches when recomputed now (schema.md §11, R15).

What does **not** count, deliberately: a `register` event, a `read` event, a cache-only fetch, an
event copied in from another run, a search snippet, an abstract already sitting in
`corpus.jsonl`, or an existing wiki note. This is the plan's Non-Goal made mechanical.

**The user-supplied-PDF exception.** For `origin: "user-supplied-pdf"` there is nothing to
re-fetch — the paper arrived as bytes and there may be no lawful URL to retrieve it from. The
immutable, hash-checked local file *is* the fresh source. Such a snapshot is fresh iff a
`local_pdf` event exists for it in this run **and** the file at `asset.path` (resolved against
the wiki root) still hashes to `asset.sha256`. A missing file, a changed file, or a `local_pdf`
event on a snapshot with `asset: null` fails the rule (`ASSET_HASH_MISMATCH`).

No part of this permits paywall circumvention, credentials, proxies or browser automation. The
exception exists because a user legitimately handing over a PDF is a real acquisition route
(`PLAN.md` rung 0 and the `inbox/` resume loop), not because retrieval rules are negotiable.

---

## 4. The span contract

```json
{ "claim": "The trial reported lower exacerbation rates.", "evidence_id": "pmid:12345678",
  "source_id": "src-3f9a1c...", "start": 10422, "end": 10610, "access": "full_text" }
```

- `start` inclusive, `end` **exclusive**; the excerpt is exactly `text[start:end]`.
- Offsets are **character offsets into the snapshot's decoded text** — Python `str` indices, i.e.
  Unicode code points. Not bytes, not UTF-8 byte counts, not UTF-16 code units, not offsets into
  a re-wrapped or markdown-rendered view (R10). This is the single ambiguity most likely to
  produce silent, plausible-looking corruption, so it is closed here and in schema.md §12.
- Maximum **2000 characters** per span. Over-length is a failure, never a truncation.
- Multiple spans per claim are ORed evidence, may come from different snapshots, may overlap, and
  are never merged or length-summed by the assembler (R18).
- `claim` is a label in the agent's own words, never a transcription of the source.
- `access` is copied from the snapshot, not chosen.

Where spans are required (R19): every extraction outcome with a non-null `effect`, `ci_low`,
`ci_high` or `p_value`; every non-null narrative factual field of an extraction; every appraisal
domain judgement that is not `unclear`. An `unclear` grounded in absent reporting takes
`spans: []` — there is nothing to point at, and saying so is the honest record.

Records written before this layer keep working: no spans means `unverified` — diagnosed as
`NO_SPANS`, never accepted, never able to back a promoted concept, and reported as a `C-SPAN`
warning while the gate is off (R16).

`quotes[]` on an extraction record is now **derived**: written by the assembler from the spans,
never by the agent (R17). The appraisal record has no quotes field at all.

---

## 5. The assembler gate

`scripts/assemble.py` reads `workspace/extractions/*.json`, `workspace/appraisals/*.json`,
`outputs/report.md`, `sources/*.json`, `events.jsonl` and `corpus.jsonl`, and writes
`<run>/outputs/result.json` (schema.md §13): `accepted[]` for artifacts that survived every
check, `diagnostics.unresolved[]` for those that did not, each with a closed `reason_code`.

Check list, one `reason_code` each:

| Check | Code |
|---|---|
| `source_id` is known | `UNKNOWN_SOURCE` |
| Snapshot hashes recompute | `SNAPSHOT_HASH_MISMATCH` |
| Span is in range | `SPAN_OUT_OF_RANGE` |
| Span ≤ 2000 characters | `SPAN_TOO_LONG` |
| Excerpt equals the re-slice | `EXCERPT_MISMATCH` |
| Record carries the spans it owes | `NO_SPANS` |
| Cited URL was retrieved for this artifact | `URL_NOT_RETRIEVED` |
| Supported verdict has a fresh retrieval | `NO_FRESH_FETCH` |
| No unsupported verifier verdict | `UNSUPPORTED_VERDICT` |
| Synthesis introduces no source outside accepted branch evidence | `SOURCE_OUTSIDE_ACCEPTED` |
| Literature claim traces to PMID/DOI/PMCID | `NO_PAPER_ID` |
| User-supplied PDF bytes still hash correctly | `ASSET_HASH_MISMATCH` |

**Derivation rule.** The assembler derives URLs, excerpts, sections, pages, access level, paper
identifiers and all other source metadata **from snapshots**, never from agent-written output.
Where the two disagree, the snapshot wins and the artifact is rejected — never silently
corrected, because a silent correction hides the fact that an agent asserted something false.

**Gate default: off (R20).** The gate is built, wired and **always reported**, but with
`gates.evidence_kernel` / `--gate` absent it does not block Stage 8. `result.json` always records
`gate.verdict`, and the coordinator always surfaces it. Two things are never downgraded by the
flag: `C-SNAPSHOT` and `C-SPAN` tamper failures block OKF promotion regardless. The gate flips to
blocking-by-default only after a live dry run passes (`VALIDATION_ARCHITECTURE_PLAN.md` migration
step 9).

**Ordering (R24).** `assemble.py` → `verify.py` (reads `result.json`, emits `C-ASSEMBLER` among
its checks into `outputs/verification.json`) → render / HTML report → `okf.py promote --check` →
`okf.py promote`. `result.json` does not replace `verification.json`; it is the evidence ledger
the verifier and the publisher both read.

---

## 6. Publisher integrity preflight

`scripts/okf.py promote --run-dir RUN --wiki WIKI --check` validates without writing anything.
Before any concept file is created it must:

1. Re-read every snapshot referenced by the concepts to be written.
2. Recompute `content_hash` and `source_id`; reject on any mismatch.
3. Re-slice every excerpt from `start:end` and compare against what `result.json` carries.
4. Reject if an agent-written excerpt differs from the source text.
5. Verify each evidence item's PMID/DOI/PMCID against the snapshot's `paper` metadata.
6. Verify user-supplied PDF bytes hash to the recorded `asset.sha256`.
7. Write with exclusive creation / atomic replace.

`--check` is mandatory before the real promote. Promotion **fails closed**: any failure aborts
the whole promotion — never a partial bundle, never a half-written concept. Already-generated run
outputs, `outputs/report.md` above all, are preserved; the run is not lost because the wiki write
was refused. The existing OKF v0.2 validators (`references/okf-bundle.md` V1–V17) still apply on
top; this preflight adds to them and weakens none.

---

## 7. Worked example: one claim, snapshot to concept

**Question.** Does group CBT reduce depressive symptoms in adolescents at 12 weeks?

**(a) Retrieval.** `fulltext.py` reaches PMC for PMID 12345678. The text is handed to
`source.py fetch`, which writes

```text
<run>/sources/src-3f9a1cb84d02e77a5c1b0f9e2d6a4413c8b7e05f9a2d1c3e4b5a6978d0e1f2a3.json
```

with `url` = the PMC article URL, `access: "full_text"`, `origin: "pmc"`,
`paper: {pmid: "12345678", doi: "10.1000/example", pmcid: "PMC1234567"}`, `asset: null` (an XML
route stored no file), and `text` = 61 042 characters. It appends

```json
{"schema_version":1,"event_id":"ev-0007","type":"fetch","source_id":"src-3f9a1c...","url":"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/","at":"2026-09-08T12:07:44Z","fresh":true,"sha256":"b7e05f...","actor":"main","detail":"pmc full text, 61042 chars, http 200"}
```

**(b) Extraction.** The extraction subagent gets `source_id`, `access`, and the two `source.py`
commands — not a file of prose. It runs
`source.py spans --source src-3f9a1c... --query "CDI-2"`, gets back a window stamped
`start=10380 end=10900`, reads it, and writes into `workspace/extractions/pmid-12345678.json`:

```json
{ "name": "CDI-2 total score", "timepoint": "12 weeks", "effect_measure": "SMD",
  "effect": -0.41, "ci_low": -0.68, "ci_high": -0.14, "p_value": 0.003,
  "direction": "favors_intervention",
  "spans": [ { "claim": "CDI-2 at 12 weeks, SMD -0.41 (95% CI -0.68 to -0.14), p=0.003.",
               "evidence_id": "pmid:12345678", "source_id": "src-3f9a1c...",
               "start": 10422, "end": 10610, "access": "full_text" } ] }
```

`quotes` is `[]`. The subagent returns one receipt and no paper text.

**(c) Appraisal.** The appraisal subagent points its `Missing outcome data` = `high` judgement at
`start=12880 end=13044`, and leaves `Selection of the reported result` = `unclear` with
`spans: []`, because the paper cites no protocol and there is nothing to point at.

**(d) Report.** Synthesis writes: *"Group CBT reduced CDI-2 scores at 12 weeks (SMD −0.41, 95% CI
−0.68 to −0.14) in one RCT of 240 adolescents [@pmid12345678]."*

**(e) Assembly.** `assemble.py` recomputes the snapshot's hashes (match), checks
`0 ≤ 10422 < 10610 ≤ 61042` (in range), `10610 − 10422 = 188 ≤ 2000` (in bounds), re-slices
`text[10422:10610]`, finds `ev-0007` fresh for this run, confirms `paper.pmid` agrees with
`evidence_id`, and writes into `accepted[]`:

```json
{ "claim": "CDI-2 at 12 weeks, SMD -0.41 (95% CI -0.68 to -0.14), p=0.003.",
  "field": "outcomes[0]", "source_id": "src-3f9a1c...", "start": 10422, "end": 10610,
  "access": "full_text",
  "excerpt": "At 12 weeks the intervention group showed a greater reduction in CDI-2 total score than controls (SMD -0.41, 95% CI -0.68 to -0.14; p=0.003).",
  "section": "Results", "page": null }
```

The `excerpt` was produced here, by slicing. No agent typed it.

**(f) Verification.** `verify.py` reads `result.json`: `C-SNAPSHOT` pass, `C-SPAN` pass (214
spans checked), `C-FRESH-FETCH` pass, `C-ASSEMBLER` pass, alongside the existing
`C-CITE-RESOLVE`, `C-FULLTEXT` and `C-HYPOTHESIS-WALL` checks.

**(g) Promotion.** `okf.py promote --check` re-reads the snapshot from disk, recomputes
`content_hash` and `source_id`, re-slices `text[10422:10610]` a second time, compares it to the
excerpt in `result.json`, and checks `pmid: 12345678` against the snapshot's `paper`. All pass,
so `okf.py promote` writes the concept with

```yaml
sources:
  - id: fulltext-12345678
    resource: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/
```

and an evidence footnote whose quoted text is the re-sliced excerpt.

**(h) The counterfactual.** Had the extractor written the quote by hand as *"…a significant
reduction in CDI-2 scores…"* while the snapshot at `[10422:10610]` reads *"a greater reduction in
CDI-2 total score"*, step (e) raises `EXCERPT_MISMATCH`, the artifact lands in
`diagnostics.unresolved[]`, `C-SPAN` reports it, and step (g) refuses the concept. Had the run
reused a snapshot from an earlier run without re-fetching, step (e) raises `NO_FRESH_FETCH`. Had
someone edited `text` in the snapshot file, step (a)'s hash no longer recomputes and every span
against it dies at `C-SNAPSHOT` — which no flag can downgrade.
