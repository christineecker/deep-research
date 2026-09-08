# schema §13 — `assembler result`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 13. `assembler result`

`<run>/outputs/result.json`, written by `scripts/assemble.py` (`VALIDATION_ARCHITECTURE_PLAN.md`
Phase 4). Reads `workspace/extractions/*.json`, `workspace/appraisals/*.json`,
`outputs/report.md`, `sources/*.json`, `events.jsonl` and `corpus.jsonl`; writes one object.

```json
{
  "schema_version": 1,
  "run_slug": "cbt-adolescent-depression",
  "generated_at": "2026-09-08T15:22:07Z",
  "gate": { "enabled": false, "verdict": "warn", "flag": "--gate" },
  "counts": { "artifacts_seen": 42, "accepted": 39, "unresolved": 3, "spans_checked": 214, "sources": 37 },
  "accepted": [
    {
      "artifact_id": "extraction:pmid:12345678",
      "kind": "extraction",
      "evidence_id": "pmid:12345678",
      "source_ids": ["src-3f9a1c..."],
      "paper": { "pmid": "12345678", "doi": "10.1000/example", "pmcid": "PMC1234567" },
      "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/",
      "access": "full_text",
      "fresh": true,
      "claims": [
        {
          "claim": "Primary outcome CDI-2 at 12 weeks, SMD -0.41 (-0.68 to -0.14).",
          "field": "outcomes[0]",
          "source_id": "src-3f9a1c...",
          "start": 10422,
          "end": 10610,
          "access": "full_text",
          "excerpt": "At 12 weeks the intervention group showed a greater reduction in CDI-2 total score than controls (SMD -0.41, 95% CI -0.68 to -0.14; p=0.003).",
          "section": "Results",
          "page": 7
        }
      ]
    }
  ],
  "diagnostics": {
    "unresolved": [
      { "artifact_id": "appraisal:pmid:45678901", "kind": "appraisal", "evidence_id": "pmid:45678901", "field": "domains[2]", "reason_code": "NO_FRESH_FETCH", "detail": "src-8c22ef... last retrieved in run 2026-08-30-asthma; no fetch event in this run." },
      { "artifact_id": "extraction:doi:10-1000-old", "kind": "extraction", "evidence_id": "doi:10.1000/old", "field": null, "reason_code": "NO_SPANS", "detail": "pre-kernel record, unverified." }
    ],
    "counts_by_reason": { "NO_FRESH_FETCH": 1, "NO_SPANS": 2 }
  },
  "sources": [
    { "source_id": "src-3f9a1c...", "content_hash": "sha256:b7e05f...", "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/", "title": "Cognitive behavioral therapy for adolescent depression: a randomized trial", "access": "full_text", "origin": "pmc", "paper": { "pmid": "12345678", "doi": "10.1000/example", "pmcid": "PMC1234567" }, "asset": null, "fresh": true, "fresh_event_id": "ev-0007" }
  ]
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `run_slug` | string | yes | Run directory name. |
| `generated_at` | string | yes | ISO-8601 UTC Z. |
| `gate` | object | yes | `{enabled: bool, verdict: "pass"\|"fail"\|"warn", flag: string}`. `enabled` reflects `--gate` / `config.json` `gates.evidence_kernel`; default `false` (R20). `verdict` is `pass` when `diagnostics.unresolved` is empty, else `fail` if `enabled` else `warn`. The verdict is **always** written and always reported to the user, whether or not it blocks. |
| `counts` | object | yes | Integer tallies: `artifacts_seen`, `accepted`, `unresolved`, `spans_checked`, `sources`. |
| `accepted` | object[] | yes | Artifacts that passed every check. `[]` is legal. |
| `diagnostics` | object | yes | `{unresolved: object[], counts_by_reason: object}`. Never empty-by-omission: an absent problem is an empty list, not a missing key. |
| `sources` | object[] | yes | Snapshot metadata for every `source_id` referenced anywhere, derived from `sources/*.json`, plus `fresh` and the `fresh_event_id` that established it (`null` when not fresh). |

### `accepted[]` entry

| Field | Type | Req | Meaning |
|---|---|---|---|
| `artifact_id` | string | yes | `<kind>:<evidence_id>` for record artifacts (`extraction:pmid:12345678`), `report:<anchor>` for a report claim (`report:L142`). Unique within `accepted[]`. |
| `kind` | enum | yes | `extraction` \| `appraisal` \| `report_claim` \| `synthesis_claim`. Closed enum. |
| `evidence_id` | string | yes | Corpus key (S9). |
| `source_ids` | string[] | yes | Every distinct snapshot backing this artifact, in first-use order. |
| `paper` | object | yes | `{pmid, doi, pmcid}` **copied from the snapshot**, not from the agent record. |
| `url` | string | yes | Copied from the snapshot. |
| `access` | enum | yes | Copied from the snapshot; where several snapshots back one artifact, the **weakest** wins, ordered `web` < `abstract` < `preprint` < `guideline` < `full_text`. |
| `fresh` | bool | yes | `true` iff every `source_id` is fresh for this run (§11). |
| `claims` | object[] | yes | One entry per resolved span; at least one entry. |

### `accepted[].claims[]` entry

| Field | Type | Req | Meaning |
|---|---|---|---|
| `claim` | string | yes | Copied verbatim from the span record's `claim`. The only agent-written string that survives into `result.json`. |
| `field` | string \| null | yes | Path to the field the span backs inside the source record: `outcomes[0]`, `domains[2]`, `limitations`; `null` for a report claim. |
| `source_id` | string | yes | Copied from the span record after validation. |
| `start` | int | yes | Copied from the span record after validation. |
| `end` | int | yes | Copied from the span record after validation; exclusive. |
| `access` | enum | yes | Copied from the snapshot, not from the span record. |
| `excerpt` | string | yes | **DERIVED.** `snapshot.text[start:end]`, re-sliced at assembly time. Never copied from an agent record. |
| `section` | string \| null | yes | Derived from the snapshot's section map when it has one, else `null`. Never guessed. |
| `page` | int \| null | yes | Derived from recorded PDF page boundaries when the snapshot has them, else `null`. Never guessed. |

### `diagnostics.unresolved[]` entry

| Field | Type | Req | Meaning |
|---|---|---|---|
| `artifact_id` | string | yes | Same grammar as `accepted[].artifact_id`. |
| `kind` | enum | yes | Same closed enum. |
| `evidence_id` | string \| null | yes | `null` when the artifact names an `evidence_id` absent from `corpus.jsonl`. |
| `field` | string \| null | yes | The offending field, when the failure is localised. |
| `reason_code` | enum | yes | Closed enum, table below. |
| `detail` | string | yes | One line, ≤300 chars, machine-greppable, naming the offending ids and numbers. |

### `reason_code` enum — the Phase 4 check list

| Code | Raised when |
|---|---|
| `UNKNOWN_SOURCE` | A span names a `source_id` with no snapshot under `sources/`. |
| `SNAPSHOT_HASH_MISMATCH` | The snapshot exists but its recomputed `content_hash` or `source_id` differs from the stored value. |
| `SPAN_OUT_OF_RANGE` | `start < 0`, `end > len(text)`, or `start >= end`. |
| `SPAN_TOO_LONG` | `end - start > 2000`. |
| `EXCERPT_MISMATCH` | An agent-written excerpt (a legacy `quotes[].text`, or an excerpt field on a report claim) differs from `text[start:end]`. |
| `NO_SPANS` | A record that §7/§8 requires to carry spans carries none — the `unverified` legacy case (R16). |
| `URL_NOT_RETRIEVED` | A citation URL in the artifact has no snapshot retrieved for that artifact in this run. |
| `NO_FRESH_FETCH` | A source backing a `supported` verdict has no fresh `fetch`/`local_pdf` event for this run (§11). |
| `UNSUPPORTED_VERDICT` | `outputs/verification.json` records an unsupported claim for this artifact. |
| `SOURCE_OUTSIDE_ACCEPTED` | A synthesis or report claim introduces a `source_id` outside the accepted evidence for its branch. |
| `NO_PAPER_ID` | A literature claim's snapshot `paper` object supplies no PMID, DOI or PMCID, or contradicts the claim's `evidence_id`. |
| `ASSET_HASH_MISMATCH` | A `user-supplied-pdf` snapshot's asset file is missing or no longer hashes to `asset.sha256`. |

### Derivation rule (binding)

> The assembler derives `url`, `excerpt`, `section`, `page`, `access`, `paper` and every other
> piece of source metadata **from the snapshots**, never from agent-written output.

Agent records contribute exactly three things to `result.json`: the `claim` string, the
`source_id`, and the `start`/`end` pair. Everything else is recomputed. Where an agent record and
a snapshot disagree about a URL, an identifier, an access level or a quote, the snapshot wins and
the artifact is **rejected** with the matching `reason_code` rather than silently corrected.

`result.json` is written atomically (temp file in `outputs/`, then `os.replace`). It is an input
to `verify.py` (check `C-ASSEMBLER`) and it is the only thing `okf.py promote` reads for
evidence — never raw agent output (`VALIDATION_ARCHITECTURE_PLAN.md` Phase 6). Ordering is fixed:
`assemble.py` runs **before** `verify.py`, which runs before render, HTML report and OKF
promotion (R24).

---
