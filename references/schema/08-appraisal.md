# schema §8 — `appraisal record`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 8. `appraisal record`

`workspace/appraisals/pmid-<pmid>.json`. Stage 6, one subagent per paper.

```json
{
  "schema_version": 1,
  "pmid": "12345678",
  "evidence_id": "pmid:12345678",
  "tool": "RoB2",
  "domains": [
    {
      "domain": "Randomization process",
      "judgement": "low",
      "rationale": "Computer-generated sequence, central allocation described.",
      "spans": [
        { "claim": "Computer-generated randomisation sequence with central allocation.", "evidence_id": "pmid:12345678", "source_id": "src-3f9a1c...", "start": 9040, "end": 9188, "access": "full_text" }
      ]
    },
    {
      "domain": "Deviations from intended interventions",
      "judgement": "some_concerns",
      "rationale": "Open-label; ITT analysis reported but adherence not described.",
      "spans": [
        { "claim": "Open-label design; analysis by intention to treat.", "evidence_id": "pmid:12345678", "source_id": "src-3f9a1c...", "start": 9402, "end": 9510, "access": "full_text" }
      ]
    },
    {
      "domain": "Selection of the reported result",
      "judgement": "unclear",
      "rationale": "No information: no protocol or registration cited.",
      "spans": []
    }
  ],
  "overall_judgement": "high",
  "grade": {
    "risk_of_bias": "serious",
    "inconsistency": "not_serious",
    "indirectness": "not_serious",
    "imprecision": "serious",
    "publication_bias": "undetected",
    "certainty": "low"
  },
  "evidence_basis": "fulltext"
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `pmid` | string \| null | yes | PMID as string; `null` for non-PubMed evidence. |
| `evidence_id` | string | yes | Links back to the corpus record. |
| `tool` | enum | yes | `RoB2` \| `ROBINS-I` \| `Newcastle-Ottawa` \| `AMSTAR-2` \| `none`. `none` = no in-scope instrument applies (narrative review, guideline, editorial, abstract-only record, cross-sectional/diagnostic/qualitative/animal/modelling design). |
| `domains` | object[] | yes | Tool-specific domains in the tool's canonical order. **Exactly `[]` when `tool == "none"`** — the reason is recorded in the appraiser receipt summary and report limitations, not as a domain or quality verdict. See `references/appraisal.md`. |
| `overall_judgement` | string | yes | Tool-appropriate overall rating: RoB2/ROBINS-I `low` \| `some_concerns` \| `moderate` \| `serious` \| `critical` \| `high` \| `unclear`; NOS a star count string (`"7/9"`); AMSTAR-2 `high` \| `moderate` \| `low` \| `critically_low`. |
| `grade` | object \| null | yes | GRADE domains for the body of evidence this study contributes to. `null` when GRADE is applied only at outcome level elsewhere. |
| `evidence_basis` | enum | yes | `fulltext` \| `abstract_only`. An `abstract_only` appraisal must set every domain not assessable from an abstract to `unclear` with rationale `"not assessable from abstract"`. |

### `domains[]` entry

| Field | Type | Req | Meaning |
|---|---|---|---|
| `domain` | string | yes | Domain name as defined by the tool. |
| `judgement` | string | yes | `low` \| `some_concerns` \| `moderate` \| `serious` \| `critical` \| `high` \| `unclear` \| `yes` \| `no` \| `partial_yes` (AMSTAR-2 items) — the tool's own vocabulary; must be one of these tokens. |
| `rationale` | string | yes | ≤300 chars, states the evidence for the judgement. Never empty; `unclear` still needs a reason. |
| `spans` | object[] | yes | Claim spans (§12) locating, in the snapshot, the reported method the judgement rests on. **Required (≥1) for every judgement other than `unclear`.** `unclear` on the grounds of absent reporting takes `spans: []` — there is nothing to point at, and that is the honest record (R19). A non-`unclear` judgement with `spans: []` is `unverified` (R16). |

`quotes` is not a field of the appraisal record. The excerpts backing an appraisal are derived by
the assembler from `domains[].spans[]` into `result.json`, never written into the appraisal file.

### `grade` sub-object

| Field | Type | Req | Meaning |
|---|---|---|---|
| `risk_of_bias` | enum | yes | `not_serious` \| `serious` \| `very_serious`. |
| `inconsistency` | enum | yes | `not_serious` \| `serious` \| `very_serious`. |
| `indirectness` | enum | yes | `not_serious` \| `serious` \| `very_serious`. |
| `imprecision` | enum | yes | `not_serious` \| `serious` \| `very_serious`. |
| `publication_bias` | enum | yes | `undetected` \| `suspected` \| `strongly_suspected`. |
| `certainty` | enum | yes | `high` \| `moderate` \| `low` \| `very_low`. |

---
