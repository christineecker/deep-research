# Standalone Repo Quickstart

This quickstart creates a new manuscript repository, imports two papers into the
general paper pool, and creates a project. It does not require `wiki-manager`.

Run commands from the skill repo root:

```bash
cd /Users/sphache/.claude/skills/deep-research
```

To run this tutorial automatically instead of typing each command:

```bash
python3 scripts/tutorial.py quickstart --repo /tmp/deep-research-demo
```

## 1. Create A Standalone Research Repo

```bash
python3 scripts/research.py init /tmp/deep-research-demo
```

Expected structure:

```text
/tmp/deep-research-demo/
  data/
    sources/
    papers/
      registry.jsonl
      pool.jsonl
      extractions/
      appraisals/
  projects/
  runs/
  exports/
```

## 2. Create A Manuscript Project

```bash
python3 scripts/research.py project create diagnostic-demo \
  --repo /tmp/deep-research-demo \
  --title "Diagnostic Demo Manuscript"
```

The project lives at:

```text
/tmp/deep-research-demo/projects/diagnostic-demo/
  protocol.md
  synthesis.md
  manuscript.qmd
  refs.bib
  corpus.lock.jsonl
  figures/
  tables/
```

## 3. Import Papers Into The General Pool

```bash
python3 scripts/registry.py import-bib \
  --repo /tmp/deep-research-demo \
  --file tutorials/fixtures/sample-refs.bib
```

This writes canonical paper records to:

```text
/tmp/deep-research-demo/data/papers/registry.jsonl
/tmp/deep-research-demo/data/papers/pool.jsonl
```

`registry.jsonl` is the source of truth. `pool.jsonl` is a generated view.

## 4. Inspect The Pool

```bash
python3 scripts/registry.py list --repo /tmp/deep-research-demo
```

You should see the imported papers with lifecycle fields such as:

```json
{
  "status": "registered",
  "metadata_status": "complete",
  "asset_status": "missing",
  "extraction_status": "not_started",
  "appraisal_status": "not_appraised"
}
```

## 5. Understand The Storage Rule

Paper identity and extraction are shared across projects:

```text
data/papers/registry.jsonl
data/papers/extractions/<paper_id>.json
```

Appraisals are project-scoped:

```text
data/papers/appraisals/<project_slug>/<paper_id>.json
```

The same paper can therefore be extracted once and appraised differently for two
different manuscript questions.

## 6. Next Tutorial

Continue with [Add Papers To The Pool](02-add-papers-to-the-pool.md).
