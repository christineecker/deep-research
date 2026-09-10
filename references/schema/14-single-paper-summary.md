# schema §14 — `single-paper summary record`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 14. `single-paper summary record`

`workspace/summaries/<evidence-id-slug>.json`. Single-paper summary profile
(`references/single-paper-summary.md`), one record per summarized paper. Assembled from an
extraction record (§7) and, when present, an appraisal record (§8) — it never introduces a
factual claim that is not already backed by one of those two records.

```json
{
  "schema_version": 1,
  "summary_id": "single-paper:pmid:12345678:journal-club",
  "evidence_id": "pmid:12345678",
  "project": "example-project",
  "purpose": "journal-club",
  "audience": "researcher",
  "source_basis": "fulltext",
  "extraction_path": "data/papers/extractions/pmid-12345678.json",
  "appraisal_path": "data/papers/appraisals/example-project/pmid-12345678.json",
  "appraisal_skipped_reason": null,
  "sections": [
    {
      "name": "bottom_line",
      "claims": [
        {
          "text": "The paper reports a parallel-group RCT of 240 adolescents.",
          "evidence_id": "pmid:12345678",
          "span_refs": ["extraction:spans:0"]
        }
      ]
    }
  ],
  "limitations": [],
  "do_not_conclude": [],
  "created_at": "2026-09-10T00:00:00Z"
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `summary_id` | string | yes | `single-paper:<evidence_id>:<purpose>`. Stable across reruns of the same paper/purpose pair. |
| `evidence_id` | string | yes | The one paper this record summarizes (S9). |
| `project` | string \| null | yes | Manuscript project slug this summary is attached to, when `--project` was given. `null` for a project-less run. |
| `purpose` | enum | yes | `clinical` \| `methods` \| `journal-club` \| `peer-review` \| `background`. Drives which sections a renderer emphasizes; never changes what claims are allowed. |
| `audience` | enum | yes | `researcher` \| `clinician` \| `student` \| `grant-writer` \| `general`. |
| `source_basis` | enum | yes | `fulltext` \| `abstract_only`, copied from the extraction's `evidence_basis` (§7). |
| `extraction_path` | string | yes | Repo- or run-relative path to the extraction record this summary was built from. |
| `appraisal_path` | string \| null | yes | Repo- or run-relative path to the appraisal record (§8), when one was produced or reused. `null` when appraisal was skipped. |
| `appraisal_skipped_reason` | enum \| null | yes | `null` when `appraisal_path` is set. Otherwise one of `no_appraise_flag` \| `abstract_only` \| `no_supported_tool` — required whenever `appraisal_path` is `null` (plan "Prompting"). |
| `sections` | object[] | yes | Ordered sections, `{name, claims[]}`. `name` is one of the thirteen names in `templates/single-paper-summary.md`'s Output Template list, lower_snake_case (`bottom_line`, `why_summarized`, `study_design_and_basis`, `population_setting_sample`, `intervention_exposure_index_test_model`, `comparator_or_reference_standard`, `outcomes_and_results`, `methods_quality_and_rob`, `limitations`, `practical_takeaways`, `what_not_to_conclude`, `provenance`). A section with nothing to say is present with `claims: []` and is rendered as "not applicable" — never omitted (S3-style: absence is explicit, not silent). |
| `sections[].claims[]` | object[] | yes | `{text, evidence_id, span_refs}`. `text` is a single factual sentence about the target paper. `evidence_id` MUST equal the record's own top-level `evidence_id` — a claim naming any other paper belongs in `provenance`'s context note, never here (verifier check, `references/single-paper-summary.md` "Verification"). `span_refs` is non-empty for every claim except a `provenance`/`limitations` claim that states an absence (e.g. "no appraisal was performed"). |
| `sections[].claims[].span_refs[]` | string[] | yes | Pointers of the form `extraction:spans:<i>`, `extraction:outcomes:<i>:spans:<j>`, or `appraisal:domains:<i>:spans:<j>` — index into the *named* record's own span-bearing arrays. Never a raw quote; the verifier re-resolves the pointer against the extraction/appraisal file itself (§12). |
| `limitations` | string[] | yes | Short bullets, each traceable to `extraction.limitations`, an `outcomes[].spans` gap, or an appraisal domain judgement. `[]` only when the extraction genuinely states none. |
| `do_not_conclude` | string[] | yes | Explicit guardrail bullets warning against generalizing beyond this one paper (plan "Prompting": "never generalize from one study to the whole field"). At minimum one entry for any `fulltext` summary with `purpose` in `clinical`/`journal-club`; may be `[]` only for `background`-purpose abstract-only summaries where §"outcomes_and_results" already carries no quantitative claim. |
| `created_at` | string | yes | S2 timestamp. |

`summary_id`, `extraction_path`, and `appraisal_path` are written once at assembly time and are
immutable per S10 — a corrected summary is a new `created_at`, produced by rerunning
`scripts/paper.py summarize` with `--force`, not a hand edit.
