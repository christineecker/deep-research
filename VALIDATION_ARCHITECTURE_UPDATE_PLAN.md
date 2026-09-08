# Validation Architecture Update Plan

Purpose: close the remaining gaps after the Python evidence-kernel implementation. The core
architecture exists, but it is not yet fully tested, fully enforced, or fully reflected in the
operator-facing docs and prompts.

Current state as of 2026-09-08:

- Implemented: `scripts/store.py`, `scripts/source.py`, `scripts/assemble.py`.
- Implemented: evidence-kernel checks in `scripts/verify.py`.
- Implemented: publisher-style preflight in `scripts/okf.py promote --check`.
- Implemented: `fulltext.py` and `library.py` can register `source_ids`.
- Implemented: tests for the store, source CLI, assembler, verifier kernel path, OKF
  promotion preflight, and check-mode no-write behavior.
- Implemented: `scripts/eval.py` plus `eval/cases.json`; the harness runs assembler,
  verifier, and `okf.py promote --check` over deterministic local fixture cases.
- Implemented: HTML quote rendering prefers `outputs/result.json` accepted artifacts and
  labels legacy/no-span quotes as unverified.
- Remaining rollout item: the evidence-kernel gate is still off by default pending a live dry
  run on a real review.

---

## Phase 1: Clean Up Plan and Workflow Documentation

Replace stale Node-oriented references with the Python implementation:

```text
scripts/source.mjs              -> scripts/source.py
scripts/runtime/store.mjs       -> scripts/store.py
scripts/runtime/tools.mjs       -> folded into scripts/source.py + scripts/store.py
scripts/assemble.mjs            -> scripts/assemble.py
```

Files to update:

```text
VALIDATION_ARCHITECTURE_PLAN.md
WORKFLOW_OVERVIEW.md
PLAN.md
README.md
SKILL.md
references/evidence-kernel.md
```

Keep the historical note that the design was inspired by `deep-research-laptop`, but make the
active command surface Python-only.

Acceptance checks:

```bash
rg -n "source\.mjs|assemble\.mjs|runtime/store\.mjs|runtime/tools\.mjs" .
```

The only remaining matches should be explicitly historical or in a decision table explaining the
Python reimplementation.

---

## Phase 2: Fix Prompt Command-Line Examples

Status: completed on 2026-09-08 for active subagent prompts. This phase is retained as a
regression checklist.

The prompts previously showed stale flags:

```text
scripts/source.py read --run ... --source ...
scripts/source.py spans --run ... --source ...
```

The actual CLI uses:

```text
scripts/source.py read  --run-dir <run> --source-id <source_id> --start <n> --end <m>
scripts/source.py spans --run-dir <run> --source-id <source_id> --query "<phrase>"
```

Files covered:

```text
references/prompts/extract.md
references/prompts/appraise.md
references/evidence-kernel.md
```

Acceptance checks:

```bash
rg -n -- "--run[[:space:]]|--source[[:space:]]" references SKILL.md README.md
python3 scripts/source.py --help
```

No stale `--run` / `--source` examples should remain.

---

## Phase 3: Add Unit Tests for `store.py`

Status: completed on 2026-09-08.

Create:

```text
tests/test_store.py
```

Minimum tests:

- `compute_source_id()` matches `"src-" + sha256(url + NUL + text)`.
- `compute_content_hash()` matches `"sha256:" + sha256(text)`.
- `write_snapshot()` uses exclusive creation and returns the same snapshot on identical writes.
- `read_snapshot()` rejects tampered `text`.
- `verify_span()` accepts valid spans.
- `verify_span()` rejects unknown source IDs.
- `verify_span()` rejects out-of-range spans.
- `verify_span()` rejects spans over 2000 characters.
- `verify_span()` rejects excerpt mismatches.
- `freshness()` fails closed when `config.json.created_at` is absent.
- `freshness()` accepts a fresh `fetch` event after `created_at`.
- `freshness()` accepts a `local_pdf` event only when the asset hash matches.

Acceptance command:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/test_store.py
```

---

## Phase 4: Add Unit Tests for `source.py`

Status: completed on 2026-09-08.

Create:

```text
tests/test_source.py
```

Minimum tests:

- `read` returns bounded windows and logs a non-fresh `read` event.
- `spans` returns candidate offsets in snapshot coordinates.
- `spans` never returns spans over 2000 characters.
- `local` ingests a PDF from the shared library and writes a fresh `local_pdf` event.
- `fetch` refuses embedded credentials.
- `fetch` refuses unsupported schemes.
- `fetch` refuses PDFs and points the caller to `library.py add` + `source.py local`.
- `fetch --fresh` writes a `fresh: true` event.

Use local `file://` fixtures where possible so the suite does not require network.

Acceptance command:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/test_source.py
```

---

## Phase 5: Add Unit Tests for `assemble.py`

Status: completed on 2026-09-08.

Create:

```text
tests/test_assemble.py
```

Minimum tests:

- Valid extraction/appraisal artifacts enter `accepted[]`.
- Extraction with no spans becomes `NO_SPANS`.
- Invented `source_id` becomes `UNKNOWN_SOURCE`.
- Tampered snapshot becomes `SNAPSHOT_HASH_MISMATCH`.
- Out-of-range span becomes `SPAN_OUT_OF_RANGE`.
- Overlong span becomes `SPAN_TOO_LONG`.
- Mismatched excerpt becomes `EXCERPT_MISMATCH`.
- Missing fresh event becomes `NO_FRESH_FETCH`.
- Literature artifact without matching PMID/DOI/PMCID becomes `NO_PAPER_ID`.
- Report/synthesis claim that cites evidence outside accepted artifacts becomes
  `SOURCE_OUTSIDE_ACCEPTED`.
- `outputs/result.json` is written atomically.
- `--strict` turns `gate.verdict` from `warn` to `fail` when unresolved artifacts exist.

Acceptance command:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/test_assemble.py
```

---

## Phase 6: Add Integration Tests for `verify.py` and `okf.py promote`

Status: completed on 2026-09-08 for deterministic local fixture coverage. The tests cover
the verifier evidence-kernel checks, gate-off/gate-on missing-assembler behavior,
publisher check-mode no-write behavior, and promotion blocking for tampered evidence. A full
live review promotion remains part of Phase 9 before flipping the default gate.

Create:

```text
tests/test_validation_pipeline.py
```

Minimum tests:

- `verify.py run` reports `C-SNAPSHOT`, `C-SPAN`, `C-FRESH-FETCH`, and `C-ASSEMBLER`.
- Gate off: missing assembler result is skipped/warned, not fatal.
- Gate on: missing assembler result fails.
- Tamper fails regardless of gate.
- `okf.py promote --check` writes nothing.
- `okf.py promote --check` rejects tampered snapshots.
- `okf.py promote --check` rejects accepted artifacts whose excerpts do not match snapshots.
- `okf.py promote --check --gate` rejects unresolved assembler artifacts.
- A valid minimal run promotes OKF concepts under `<wiki>/research/`.

Acceptance command:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/test_validation_pipeline.py
```

---

## Phase 7: Implement `scripts/eval.py`

Status: completed on 2026-09-08.

Create:

```text
scripts/eval.py
eval/cases.json
```

Purpose: fixture-based smoke harness for the validation architecture. This is not a benchmark;
it verifies that the normal pipeline paths reject unsupported evidence and accept valid evidence.

Minimum fixture cases:

- precise fact with valid span
- invented source
- tampered snapshot
- stale source with no fresh event
- user-supplied PDF
- bounded literature claim with PMID/DOI
- literature claim missing identifiers
- synthesis introduces outside source

Expected command:

```bash
python3 scripts/eval.py --cases eval/cases.json --out /private/tmp/deep-research-eval
```

The harness creates temporary run directories, writes fixtures, runs `assemble.py`,
`verify.py`, and `okf.py promote --check`, then emits a JSON summary.

Acceptance command:

```bash
python3 scripts/eval.py --cases eval/cases.json --out /private/tmp/deep-research-eval
```

---

## Phase 8: Update HTML Quote Rendering

Status: completed before this update and locked by review. `scripts/html_report.py` already
uses `outputs/result.json` as the primary source for accepted extraction quotes, reconstructs
derived excerpts from accepted claims where needed, and labels span-less legacy quotes as
unverified text rather than verified evidence.

`html_report.py` currently treats `extraction.quotes[]` as author-written "Verbatim anchors".
After the evidence kernel, quote text must be assembler-derived from snapshot spans.

Update behavior:

- Prefer `outputs/result.json` accepted artifacts when present.
- Render quote/excerpt text from accepted claim records that already passed `store.verify_span`.
- Show `source_id`, `start`, `end`, `access`, and `evidence_id` in expandable metadata.
- If no `result.json` exists, label quote sections as `unverified pre-kernel anchors`.
- Never render agent-transcribed quote text as verified evidence.

Acceptance checks:

```bash
python3 scripts/html_report.py build --run-dir <fixture-run> --stdout
rg -n "unverified pre-kernel|source_id|start|end" <generated-html>
```

---

## Phase 9: Decide When to Enable the Gate by Default

Status: not flipped. The evidence-kernel gate is currently off by default. Keep it off until
one live dry run passes after the deterministic suite.

Gate rollout sequence:

1. Run all unit and integration tests. Completed on 2026-09-08.
2. Run `scripts/eval.py`. Completed on 2026-09-08.
3. Run one live `fast` profile review.
4. Confirm `assemble.py run --strict` passes or produces only expected unresolved artifacts.
5. Confirm `verify.py run --gate` passes.
6. Confirm `okf.py promote --check --gate` passes.
7. Flip the default in `SKILL.md`, `verify.py`, `assemble.py`, and `okf.py` docs/config handling.

Acceptance checks after flipping:

```bash
python3 scripts/assemble.py run --run-dir <run>
python3 scripts/verify.py run --run-dir <run>
python3 scripts/okf.py promote --run-dir <run> --wiki <wiki> --check
```

The default should behave like the current explicit `--gate` path.

---

## Phase 10: Full Regression Command

Status: completed on 2026-09-08.

Once the above phases are complete, the standard local regression should be:

```bash
python3 -m py_compile scripts/*.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/okf.py selftest
python3 scripts/eval.py --cases eval/cases.json --out /private/tmp/deep-research-eval
```

All commands must pass without credentials and without network access, except for the separate
explicit live dry run.

---

## Done Criteria

The update is complete when:

- No active documentation or prompt examples reference the old `.mjs` command surface.
- Subagent prompts use the real `source.py` flags.
- Unit tests cover `store.py`, `source.py`, `assemble.py`, `verify.py`, and `okf.py` promotion.
- `scripts/eval.py` exists and runs fixture cases without network.
- `html_report.py` treats agent-written quotes as unverified unless they came through the
  assembler result.
- `assemble.py`, `verify.py`, and `okf.py promote --check` agree on pass/fail behavior.
- The gate is explicitly still off with a documented blocker: run one live review dry run,
  then flip the default only if the strict/gated path passes or produces only expected
  unresolved artifacts.
