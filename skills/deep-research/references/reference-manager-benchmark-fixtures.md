# Reference manager v2 — benchmark fixtures (Phase 0)

Defines the validation targets referenced by `REFERENCE_MANAGER_V2_PLAN.md`
Phase 0 ("Define a benchmark fixture: proposed metadata scales of 10k and
100k papers, plus a documented representative PDF corpus. These are
validation targets, not promised capacity limits.") and used again in
Phase 4's latency measurements.

## Metadata-scale fixtures (synthetic) — built

`skills/deep-research/scripts/benchmark_refmgr.py` (`index`/`coverage`/`diff`
subcommands) is the generator this section specified: deterministic
(fixed-seed, closed-vocabulary) synthetic `Paper`/`Identifier` rows at 10k
and 100k scale, plus a small real-snapshot-file corpus for the
incomplete-chunk-coverage workload. It does not stub in `Attachment`/tag/
collection distributions as originally sketched below — the actual
bottleneck it found and fixed (`SearchRepository.rebuild()` was O(n²); see
`BENCHMARK_RESULTS.md` at the repo root) lived in `papers_fts` indexing, not
attachment/organization queries, so the fixture was scoped to what the
measurement needed rather than the full distribution originally proposed.
Revisit the richer distribution below only if a future benchmark actually
needs it.

Original spec (for reference, not current shape):

- **10k-paper fixture**: 10,000 `Paper` rows with plausible field
  distributions (year range 1990–2026, ~30% missing abstract, ~5% missing
  DOI, realistic author-count distribution 1–12), each with 1 `Identifier`
  (DOI), ~60% with one `Attachment` (no real PDF bytes — a checksum and
  page-count stub is enough for metadata-scale query benchmarks), and a
  long-tail tag/collection distribution (80% of papers in 0–1 collections,
  a small number of "power user" collections with hundreds of members).
- **100k-paper fixture**: same generator, scaled 10x, used only for the
  Phase 4 latency/index-size measurements — not required for everyday
  development or CI.
- Generation must be deterministic (fixed seed) so benchmark runs are
  comparable across changes. Do not use wall-clock time or randomness
  without a fixed seed (matches the project's existing constraint against
  non-deterministic script behavior).

## Representative PDF corpus

A small, legally distributable set of real PDFs to exercise the parts a
synthetic metadata fixture can't: rendering, OCR, and figure capture. Needed
before Phase 3 (reading/figures) and the Phase 6 release fixture library.
Target composition (exact sourcing deferred to when Phase 3 work starts):

- A handful (~10–20) of open-access papers (e.g. from PMC/arXiv, which
  permit redistribution) covering:
  - Standard single-column and two-column text layouts.
  - At least one scanned-only (image-based) PDF requiring OCR.
  - At least one with rotated pages.
  - At least one with mixed page sizes within a single document.
  - At least one with a large embedded figure/table spanning most of a page.
  - At least one malformed/truncated PDF (to exercise error handling, not
    successful rendering).
  - At least one multi-version case (preprint + published version of the
    same work, as two separate attachments).

This corpus is a **validation target**, not a promised capacity or coverage
guarantee — actual performance numbers must be measured and recorded (per
Phase 4's acceptance criteria: cold/warm query latency, index time, memory,
disk usage on named hardware) rather than assumed from this spec.

## Where these live

Synthetic fixtures: `skills/deep-research/scripts/benchmark_refmgr.py`, a
generator script, not checked-in data — matches the plan above. The real
PDF corpus is still not built; when it is, it belongs under a `fixtures/`
directory outside the installed plugin path — user-facing/application data
stays separate from the plugin directory (see
`skills/deep-research/references/fixtures/compat-trial/` for the precedent
this follows).
