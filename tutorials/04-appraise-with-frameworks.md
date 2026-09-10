# Appraise With Frameworks

Stage 6 chooses the appraisal tool from the extracted study design. Appraisal is
not a universal paper property; it is scoped to a project and often to a specific
outcome, index test, or model.

## Current Tool Map

| Design | Tool |
|---|---|
| Randomized trial | RoB 2 |
| Non-randomized intervention/exposure study | ROBINS-I |
| Cohort or case-control etiology/prognosis study | Newcastle-Ottawa |
| Systematic review | AMSTAR-2 |
| Diagnostic accuracy study | QUADAS-2 |
| Prediction model development/validation | PROBAST |
| Qualitative study | CASP qualitative checklist |
| Prevalence estimate | JBI prevalence checklist |
| Analytical cross-sectional association | JBI analytical cross-sectional checklist |
| Narrative review, guideline, case report, abstract-only record | `none` |

## Diagnostic Accuracy Example

QUADAS-2 appraises a specific index test against a reference standard.

```bash
mkdir -p /tmp/deep-research-demo/runs/tutorial-run/workspace/appraisals
cp tutorials/fixtures/sample-appraisal-quadas2.json \
  /tmp/deep-research-demo/runs/tutorial-run/workspace/appraisals/pmid-12345678.json
cp tutorials/fixtures/sample-corpus.jsonl \
  /tmp/deep-research-demo/runs/tutorial-run/corpus.jsonl
```

Promote the appraisal into the project-scoped appraisal store:

```bash
python3 scripts/registry.py appraise-promote \
  --repo /tmp/deep-research-demo \
  --run-dir /tmp/deep-research-demo/runs/tutorial-run \
  --project diagnostic-demo
```

The canonical appraisal path is:

```text
/tmp/deep-research-demo/data/papers/appraisals/diagnostic-demo/pmid-12345678.json
```

The registry record stores this under its `appraisals` map:

```json
{
  "appraisals": {
    "diagnostic-demo": "data/papers/appraisals/diagnostic-demo/pmid-12345678.json"
  }
}
```

## Qualitative Example

CASP qualitative appraises the qualitative study as a checklist count, not as a
risk-of-bias label.

```bash
cp tutorials/fixtures/sample-appraisal-casp.json \
  /tmp/deep-research-demo/runs/tutorial-run/workspace/appraisals/pmid-55501234.json
```

This fixture is useful for reading the shape of CASP output. Promote it only
after the corresponding paper exists in the registry and run corpus.

## Abstract-Only Rule

If only an abstract exists, do not force a checklist.

Use:

```json
{
  "tool": "none",
  "domains": [],
  "overall_judgement": "unclear",
  "evidence_basis": "abstract_only"
}
```

This records that the paper was not appraised by an in-scope full-text
instrument.
