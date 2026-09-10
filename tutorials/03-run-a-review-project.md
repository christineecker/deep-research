# Run A Review Project

This tutorial shows how a project uses the general paper pool. The full research
pipeline still produces run-local workspace files first; finished records are
then promoted into the canonical repo.

## 1. Create Or Choose A Project

```bash
python3 scripts/research.py project create diagnostic-demo \
  --repo /tmp/deep-research-demo \
  --force
```

Project files live under:

```text
/tmp/deep-research-demo/projects/diagnostic-demo/
```

## 2. Draft The Protocol

Copy the fixture protocol into the project as a starting point:

```bash
cp tutorials/fixtures/sample-protocol.md \
  /tmp/deep-research-demo/projects/diagnostic-demo/protocol.md
```

The protocol records:

- the user question
- the selected question framework
- inclusion and exclusion criteria
- search strategy
- planned appraisal tools

## 3. Stage Run Output Locally

During a real run, extraction records first appear under the run workspace:

```text
/tmp/deep-research-demo/runs/<run_slug>/workspace/extractions/
/tmp/deep-research-demo/runs/<run_slug>/workspace/appraisals/
```

The fixture below shows the shape of a diagnostic-accuracy extraction:

```bash
mkdir -p /tmp/deep-research-demo/runs/tutorial-run/workspace/extractions
cp tutorials/fixtures/sample-extraction-quadas2.json \
  /tmp/deep-research-demo/runs/tutorial-run/workspace/extractions/pmid-12345678.json
cp tutorials/fixtures/sample-corpus.jsonl \
  /tmp/deep-research-demo/runs/tutorial-run/corpus.jsonl
```

In a real pipeline run, the extraction subagent writes this file and the verifier
checks its source spans. The run corpus points at the workspace extraction via
`extraction_path`; promotion follows that pointer rather than scanning every JSON
file in the workspace.

## 4. Promote Extraction Into The General Pool

For this synthetic fixture, use `--no-verify` because it does not include a real
source snapshot in `/tmp/deep-research-demo/data/sources/sources/`.

```bash
python3 scripts/registry.py promote \
  --repo /tmp/deep-research-demo \
  --run-dir /tmp/deep-research-demo/runs/tutorial-run \
  --no-verify
```

Promoted extraction records live at:

```text
/tmp/deep-research-demo/data/papers/extractions/pmid-12345678.json
```

The registry now points to that canonical extraction path.

## 5. Reuse Existing Pool Records In A Later Run

```bash
python3 scripts/pool.py lookup \
  --repo /tmp/deep-research-demo \
  --pmid 12345678
```

To copy a pooled extraction into a new run workspace:

```bash
python3 scripts/pool.py reuse \
  --repo /tmp/deep-research-demo \
  --run-dir /tmp/deep-research-demo/runs/second-run \
  --pmid 12345678
```

This is how a manuscript project can combine newly ingested papers with papers
that were already in the general pool.
