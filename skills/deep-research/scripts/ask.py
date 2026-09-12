#!/usr/bin/env python3
"""ask.py — answer a question from the papers already in a standalone repo.

Every other answering path in this skill is either the full eight-stage pipeline
(`SKILL.md`) or a single-paper summary (`paper.py summarize`). This is the third one:
the library already holds extracted, span-verified, appraised papers, and a question
about what they say should not require a new run.

**This script retrieves and verifies; it does not write prose.** It returns a bundle of
candidate evidence — claims and passages, each carrying the `(source_id, start, end)`
span it came from and the result of re-verifying that span against the snapshot store —
and the caller (`/deep-research:ask`, `commands/ask.md`) writes the answer using only
what is in the bundle. Scripts do network and state work; the agent does the reasoning.
That split is why there is no model call anywhere in this file.

Retrieval is hybrid (OPTIMIZATION_PLAN.md item 6):

  * **lexical** — refmgr's bm25 chunk index (`refmgr/repositories/chunks.py`), which
    indexes full-text snapshot bodies at chunk granularity;
  * **semantic** — `embeddings.jsonl`'s cached paper vectors, queried with the question
    itself via `embeddings.py`'s `query` machinery;

fused by reciprocal rank. Semantic retrieval is optional in every sense: no embeddings
file, or no `sentence-transformers` installed, degrades to lexical-only with a note in
the output, never an error.

Verification reuses the evidence kernel unchanged (`store.py verify_span`,
`references/evidence-kernel.md`): a span whose snapshot no longer hashes, whose offsets
no longer fit, or whose text no longer matches is reported under `unverified` and must
not be cited. Nothing here decides what is true — it decides what is still quotable.

Subcommands
  retrieve  --repo --question "<text>" [--k N] [--passages-per-paper N]
            [--project <slug>] [--no-semantic]

Environment: python3, stdlib only. The optional semantic leg imports
`sentence-transformers` lazily, through `embeddings.py`'s existing carve-out
(`references/acquisition.md` §9) — this script adds no new dependency of its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registry as _registry  # noqa: E402  (Registry, repo_paths, extraction helpers)
import store as _store  # noqa: E402  (global_read_snapshot, verify_span)

SCHEMA_VERSION = 1

#: Reciprocal-rank-fusion constant. 60 is the value from the original RRF paper and is
#: the usual default; it damps the head of each list so one confident ranker cannot
#: drown out agreement between both.
RRF_K = 60

DEFAULT_PAPERS = 8
DEFAULT_PASSAGES_PER_PAPER = 3
#: How deep to go in each individual ranker before fusing. Wider than `--k` on purpose:
#: fusion is only interesting where the two lists disagree.
CANDIDATE_DEPTH = 40


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


# ----------------------------------------------------------------- retrieval legs


def lexical_candidates(service, question: str, depth: int = CANDIDATE_DEPTH) -> tuple[list[str], dict]:
    """Paper ids ranked by the best bm25 chunk hit each one has, plus the raw hits.

    Papers are ranked by their *best* chunk rather than by a sum over chunks: one
    strongly on-point passage is the signal worth chasing, and summing rewards long
    papers for being long.
    """
    hits = service.chunks.search(question, limit=depth * 4)
    order: list[str] = []
    by_paper: dict[str, list[dict]] = {}
    for hit in hits:
        paper_id = hit["paper_id"]
        if paper_id not in by_paper:
            by_paper[paper_id] = []
            order.append(paper_id)
        by_paper[paper_id].append(hit)
    return order[:depth], by_paper


def semantic_candidates(repo_root: Path, question: str, depth: int = CANDIDATE_DEPTH) -> tuple[list[str], dict]:
    """Evidence ids ranked by cosine similarity to the question, plus a status note.

    Returns `([], {"status": ...})` for every "not available" case — no embeddings
    file, mixed models, package missing. The caller keeps going; a hybrid retriever
    that hard-fails when half of it is unconfigured would be worse than a lexical one.
    """
    import embeddings as _embeddings

    if not _embeddings.embeddings_path(repo_root).exists():
        return [], {"status": "unavailable",
                    "detail": "no embeddings.jsonl — run `embeddings.py index` to enable "
                              "semantic retrieval"}
    cached = _embeddings.read_embeddings(repo_root)
    model_name, error = _embeddings._cached_model(cached, None)
    if error is not None:
        return [], {"status": "unavailable", "detail": error}
    try:
        model = _embeddings._load_sentence_transformer(model_name)
    except SystemExit:
        return [], {"status": "unavailable",
                    "detail": "sentence-transformers is not installed; lexical retrieval "
                              "only (see references/reference-manager.md)"}

    vector = [float(x) for x in model.encode([question], show_progress_bar=False)[0]]
    scored = []
    for eid, rec in cached.items():
        if rec.get("model") != model_name:
            continue
        try:
            scored.append((_embeddings.cosine_similarity(vector, rec["vector"]), eid))
        except (ValueError, KeyError, TypeError):
            continue
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    ranked = [eid for _, eid in scored[:depth]]
    return ranked, {"status": "ok", "model": model_name, "compared": len(scored)}


def reciprocal_rank_fusion(*ranked_lists: list[str]) -> list[tuple[str, float]]:
    """Fuse ranked id lists into one, best first. `sum(1 / (RRF_K + rank))` per id.

    Rank-based rather than score-based on purpose: bm25 scores and cosine similarities
    are not on a comparable scale, and normalizing them would invent a relationship
    that isn't there.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for position, key in enumerate(ranked, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + position)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


# ----------------------------------------------------------------- verification


class _SnapshotCache:
    """Read each snapshot at most once per `ask` invocation.

    A read that fails is cached as its *error*, not as "missing": a snapshot whose
    content no longer hashes is a tampered or corrupted source, and saying so is the
    whole point of the check. Collapsing it to UNKNOWN_SOURCE would hide it.
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self._cache: dict[str, dict] = {}

    def get(self, source_id: str) -> tuple[dict | None, dict | None]:
        """`(snapshot, failure)` — exactly one of the two is not None."""
        if source_id not in self._cache:
            try:
                self._cache[source_id] = {
                    "snapshot": _store.global_read_snapshot(self.repo_root, source_id)}
            except _store.StoreError as exc:
                self._cache[source_id] = {"failure": {
                    "ok": False, "reason_code": exc.reason_code or "UNKNOWN_SOURCE",
                    "detail": exc.message}}
            except Exception as exc:
                self._cache[source_id] = {"failure": {
                    "ok": False, "reason_code": "UNKNOWN_SOURCE", "detail": str(exc)}}
        entry = self._cache[source_id]
        return entry.get("snapshot"), entry.get("failure")


def verify(span: dict, snapshots: _SnapshotCache, *, excerpt: str | None = None) -> dict:
    """Re-verify one span against its snapshot. Returns the kernel's own result dict.

    `store.verify_span`'s `run_dir` argument is unused when a snapshot is supplied,
    which is the case here — `ask` is repo-scoped and has no run directory.
    """
    source_id = span.get("source_id")
    if not isinstance(source_id, str):
        return {"ok": False, "reason_code": "SCHEMA_ERROR",
                "detail": "span record has no source_id"}
    snapshot, failure = snapshots.get(source_id)
    if snapshot is None:
        return failure
    return _store.verify_span(snapshots.repo_root, span, excerpt=excerpt, snapshot=snapshot)


# ----------------------------------------------------------------- bundle assembly


def _paper_claims(rec: dict, repo_root: Path, snapshots: _SnapshotCache) -> tuple[list[dict], list[dict]]:
    """Verified extraction claims for one record, and the ones that failed verification."""
    extraction = _registry._extraction_data(rec, repo_root)
    if not extraction:
        return [], []
    verified, failed = [], []
    for span in _registry._extraction_spans(extraction):
        claim = span.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            continue
        result = verify(span, snapshots)
        entry = {
            "claim": claim.strip(),
            "source_id": span.get("source_id"),
            "start": span.get("start"),
            "end": span.get("end"),
            "access": span.get("access"),
        }
        if result["ok"]:
            entry["excerpt"] = result.get("excerpt")
            verified.append(entry)
        else:
            entry["reason_code"] = result.get("reason_code")
            entry["detail"] = result.get("detail")
            failed.append(entry)
    return verified, failed


def _paper_passages(hits: list[dict], snapshots: _SnapshotCache,
                    limit: int) -> tuple[list[dict], list[dict]]:
    """Verified full-text passages from this paper's chunk hits, best first."""
    verified, failed = [], []
    for hit in hits:
        if len(verified) >= limit:
            break
        span = {"source_id": hit["source_id"], "start": hit["start"], "end": hit["end"]}
        result = verify(span, snapshots, excerpt=hit["text"])
        entry = {
            "source_id": hit["source_id"], "start": hit["start"], "end": hit["end"],
            "snippet": hit["snippet"], "score": hit["score"],
        }
        if result["ok"]:
            entry["text"] = hit["text"]
            verified.append(entry)
        else:
            entry["reason_code"] = result.get("reason_code")
            entry["detail"] = result.get("detail")
            failed.append(entry)
    return verified, failed


def cmd_retrieve(args) -> int:
    repo_root = _registry.repo_paths(args.repo)["repo_root"]
    question = (args.question or "").strip()
    if not question:
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "ask",
              "error": "--question must be non-empty"})
        return 2

    registry = _registry.Registry(repo_root)
    if not registry.records:
        emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "ask",
              "repo": str(repo_root), "question": question, "papers": [],
              "notes": ["the registry is empty — nothing to answer from"]})
        return 0

    by_paper_id = {rec["refmgr_paper_id"]: rec for rec in registry.records.values()
                   if rec.get("refmgr_paper_id")}
    notes: list[str] = []

    service = None
    lexical_order: list[str] = []
    chunk_hits: dict[str, list[dict]] = {}
    if (_registry.repo_paths(repo_root)["refmgr"] / "library.sqlite3").exists():
        service = _registry._refmgr_service(repo_root)
    try:
        if service is not None and service.chunks.coverage()["chunks"] > 0:
            lexical_order, chunk_hits = lexical_candidates(service, question)
        else:
            notes.append("no full-text chunk index — run `registry.py reindex --repo "
                         f"{repo_root}` to enable passage retrieval")
    finally:
        if service is not None:
            service.close()

    # Both legs are fused in evidence_id space: the chunk index is keyed by refmgr
    # paper id, the embeddings cache by evidence_id, and the registry record is what
    # joins them.
    lexical_eids = [by_paper_id[pid]["evidence_id"] for pid in lexical_order
                    if pid in by_paper_id]

    semantic_eids: list[str] = []
    semantic_status = {"status": "skipped", "detail": "--no-semantic"}
    if not args.no_semantic:
        semantic_eids, semantic_status = semantic_candidates(repo_root, question)
        semantic_eids = [eid for eid in semantic_eids if eid in registry.records]
        if semantic_status["status"] != "ok":
            notes.append(semantic_status["detail"])

    fused = reciprocal_rank_fusion(lexical_eids, semantic_eids)
    if not fused:
        emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "ask",
              "repo": str(repo_root), "question": question, "papers": [],
              "retrieval": {"lexical": len(lexical_eids), "semantic": len(semantic_eids),
                            "semantic_status": semantic_status},
              "notes": notes + ["retrieval returned nothing for this question"]})
        return 0

    snapshots = _SnapshotCache(repo_root)
    papers, unverified = [], []
    for rank, (eid, score) in enumerate(fused[: args.k], start=1):
        rec = registry.records.get(eid)
        if rec is None:
            continue
        claims, bad_claims = _paper_claims(rec, repo_root, snapshots)
        hits = chunk_hits.get(rec.get("refmgr_paper_id") or "", [])
        passages, bad_passages = _paper_passages(hits, snapshots, args.passages_per_paper)
        for entry in bad_claims + bad_passages:
            unverified.append({"evidence_id": eid, **entry})
        papers.append({
            "evidence_id": eid,
            "rank": rank,
            "fused_score": round(score, 6),
            "found_by": [name for name, ids in (("lexical", lexical_eids),
                                                ("semantic", semantic_eids)) if eid in ids],
            "title": rec.get("title"),
            "journal": rec.get("journal"),
            "publication_date": rec.get("publication_date"),
            "extraction_status": rec.get("extraction_status"),
            "appraised": bool((rec.get("appraisals") or {}).get(args.project)) if args.project
                         else rec.get("appraisal_status") == "appraised",
            "claims": claims,
            "passages": passages,
        })

    if unverified:
        notes.append(f"{len(unverified)} span(s) failed re-verification and must not be "
                     "cited; see `unverified`")
    if not any(p["claims"] or p["passages"] for p in papers):
        notes.append("papers matched, but no span survived verification — answer only "
                     "that the library has nothing quotable on this question")

    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "ask",
          "repo": str(repo_root), "question": question,
          "retrieval": {"lexical": len(lexical_eids), "semantic": len(semantic_eids),
                        "semantic_status": semantic_status, "fused": len(fused)},
          "papers": papers, "unverified": unverified, "notes": notes})
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ask.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("retrieve", help="verified evidence for a question, from the repo")
    s.add_argument("--repo", required=True)
    s.add_argument("--question", required=True)
    s.add_argument("--k", type=int, default=DEFAULT_PAPERS,
                   help=f"how many papers to return (default: {DEFAULT_PAPERS})")
    s.add_argument("--passages-per-paper", dest="passages_per_paper", type=int,
                   default=DEFAULT_PASSAGES_PER_PAPER,
                   help=f"verified passages per paper (default: {DEFAULT_PASSAGES_PER_PAPER})")
    s.add_argument("--project", help="report appraisal status scoped to this project")
    s.add_argument("--no-semantic", dest="no_semantic", action="store_true",
                   help="lexical retrieval only; never load the embedding model")
    s.set_defaults(func=cmd_retrieve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
