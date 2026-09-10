# schema §15 — `paper summary set record`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 15. `paper summary set record`

`workspace/summary-set.json`. Selected-paper summary profile
(`references/single-paper-summary.md` "Selected-paper profile"), one record per
`scripts/paper.py summarize-set` run. A manifest plus an optional bounded orientation layer over
already-verified §14 records — it is never a substitute for full Stage 2/3/7 review artifacts.

```json
{
  "schema_version": 1,
  "set_id": "paper-set:topic:adolescent-cbt:2026-09-10",
  "project": "example-project",
  "selection_mode": "topic",
  "selection_basis": {
    "topic": "adolescent CBT depression trials",
    "question": null,
    "ids_file": null,
    "database": "pubmed",
    "retrieved_at": "2026-09-10T00:00:00Z",
    "limit": 10
  },
  "included_evidence_ids": ["pmid:12345678", "pmid:23456789"],
  "failed_identifiers": [
    { "identifier": "pmid:99999999", "stage": "retrieve", "reason": "no full text or abstract obtainable" }
  ],
  "excluded_candidates": [
    { "evidence_id": "pmid:34567890", "reason": "not selected for bounded summary set" }
  ],
  "summary_paths": [
    "workspace/summaries/pmid-12345678.json",
    "workspace/summaries/pmid-23456789.json"
  ],
  "overview": {
    "enabled": true,
    "claims": []
  },
  "created_at": "2026-09-10T00:00:00Z"
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `set_id` | string | yes | `paper-set:<selection_mode>:<slug>:<YYYY-MM-DD>`. `<slug>` is `_common.slugify` of the explicit-set label, question, or topic. |
| `project` | string \| null | yes | Manuscript project slug, when `--project` was given. |
| `selection_mode` | enum | yes | `explicit` \| `question` \| `topic`. `explicit` = user-supplied PMID/DOI/PMCID/evidence-id/PDF/BibTeX/folder inputs. `question`/`topic` = discovery-assisted (plan "Product Shape"). |
| `selection_basis` | object | yes | See below. |
| `selection_basis.topic` | string \| null | yes | The `--topic` value. `null` unless `selection_mode: topic`. |
| `selection_basis.question` | string \| null | yes | The `--question` value. `null` unless `selection_mode: question`. |
| `selection_basis.ids_file` | string \| null | yes | Run-relative path to `--ids-file`/`--bib`/`--folder` input, when the explicit set came from a file rather than repeated flags. `null` otherwise. |
| `selection_basis.database` | string \| null | yes | Source database queried for discovery-assisted modes (`pubmed`). `null` for `explicit`. |
| `selection_basis.retrieved_at` | string \| null | yes | S2 timestamp of the discovery query. `null` for `explicit`. |
| `selection_basis.limit` | int \| null | yes | The bounded cap applied (`--limit`, else the hard default). `null` for `explicit`, where the user's own input list is the only bound. |
| `included_evidence_ids` | string[] | yes | Every evidence_id that reached a verified §14 summary. Order matches `summary_paths`. |
| `failed_identifiers` | object[] | yes | `{identifier, stage, reason}` for every requested identifier that did not reach a verified summary. `stage` is one of `normalize` \| `register` \| `retrieve` \| `extract` \| `appraise` \| `assemble` \| `verify`. `[]` when every requested identifier succeeded. Set-level failures never block the identifiers that did succeed unless `--strict` was passed (plan "Pool Integration"). |
| `excluded_candidates` | object[] | yes | `{evidence_id, reason}` for candidates a discovery-assisted query surfaced but did not select (over `--limit`, or explicitly deselected). `[]` for `explicit` mode. |
| `summary_paths` | string[] | yes | Run-relative paths to each included paper's §14 record, same order as `included_evidence_ids`. |
| `overview.enabled` | bool | yes | Whether `--overview` produced an orientation section. `false` when `--no-overview` or the default policy left it off. |
| `overview.claims` | object[] | yes | `{text, evidence_ids, basis}` — same shape as §14 claims but `evidence_ids` (plural) may name more than one included paper, since this is the one place a set-level record compares descriptive attributes across papers. `basis` names the descriptive attribute compared (`design`, `population`, `intervention`, `outcomes_measured` — plan "Output Template"). `[]` when `overview.enabled` is `false`. Never contains a pooled-effect, certainty, or consensus claim (`references/single-paper-summary.md` "Verification"). |
| `created_at` | string | yes | S2 timestamp. |

A `paper summary set record` never carries GRADE tables, PRISMA counts, or an effect-direction
tabulation — those remain `references/synthesis.md` / §13 (`assembler result`) artifacts, produced
only when the full review pipeline (`SKILL.md` Stage 7) actually ran.
