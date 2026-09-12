#!/usr/bin/env python3
"""embeddings.py — semantic similarity search over the registry at
`<repo>/data/papers/embeddings.jsonl`.

Standalone-repo companion to `registry.py`, following the same conventions (`Registry`,
`advisory_lock`, atomic tmp+replace, `--repo` required everywhere). Builds one embedding
vector per registered paper from title + abstract + extraction narrative fields, caches it,
and answers nearest-neighbour queries against the cache with pure-Python cosine similarity
(no numpy needed at personal-library scale).

Subcommands
  index    --repo (--model <name> --limit N --force)   (re)embed registry records
  similar  --repo --evidence-id <id> [--k N]            top-k nearest neighbours

Embedding text per paper: `title`. `abstract`. plus, when `extraction_path` is set and the
file exists, its narrative fields (`population`, `intervention`, `comparator`,
`limitations`, `extractor_notes`) — read directly as JSON, no shared loader needed for a
handful of string fields (`references/schema/07-extraction.md` §7).

Record shape (`data/papers/embeddings.jsonl`, one line per evidence_id):
    {"schema_version": 1, "evidence_id": "pmid:12345678", "model": "all-MiniLM-L6-v2",
     "dim": 384, "vector": [...], "updated_at": "2026-09-12T00:00:00Z"}

Environment: python3, stdlib only for `similar` and all CLI plumbing. `index` additionally
requires the optional third-party package `sentence-transformers`
(`pip install sentence-transformers`), imported lazily only inside the functions that need
it — importing this module, or running any `--help`, never requires it to be installed.

This is a **narrow, deliberate, documented exception** to deep-research's blanket
"zero pip installs, ever" policy (`references/acquisition.md` §9), scoped to this one
script for semantic similarity search only — see `references/reference-manager.md`. No
other script in this skill gains a third-party dependency because of this file.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import utcnow  # noqa: E402  (sibling module, stdlib-only)
from registry import Registry, advisory_lock, repo_paths  # noqa: E402  (reuse, do not reimplement)

SCHEMA_VERSION = 1
DEFAULT_MODEL = "all-MiniLM-L6-v2"

_NARRATIVE_FIELDS = ("population", "intervention", "comparator", "limitations", "extractor_notes")

_ST_IMPORT_ERROR = (
    "embeddings.py requires 'sentence-transformers' (pip install sentence-transformers) "
    "— this is a scoped, deliberate exception to deep-research's no-pip-installs policy, "
    "isolated to this one script. See references/reference-manager.md."
)


def emit(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


def embeddings_path(repo_root: Path) -> Path:
    return repo_paths(repo_root)["papers"] / "embeddings.jsonl"


def _load_sentence_transformer(model_name: str):
    """Lazy import — never at module top level, so `--help` and every non-`index`/`similar`
    code path work with no third-party package installed."""
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError:
        print(_ST_IMPORT_ERROR, file=sys.stderr)
        raise SystemExit(1)
    return SentenceTransformer(model_name)


def _extraction_narrative_text(extraction_path: str | None, repo_root: Path) -> str:
    if not extraction_path:
        return ""
    path = Path(extraction_path)
    if not path.is_absolute():
        path = repo_root / path
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    parts = []
    for field in _NARRATIVE_FIELDS:
        val = data.get(field)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    return " ".join(parts)


def embedding_text(rec: dict, repo_root: Path) -> str:
    """Title + abstract + extraction narrative fields, in that order (plan §1 "Similarity
    search"). Never raises on a paper missing some of these — an empty string is a valid
    (if useless) embedding input, filtered out by the caller instead."""
    parts = []
    title = rec.get("title")
    if isinstance(title, str) and title.strip():
        parts.append(title.strip())
    abstract = rec.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        parts.append(abstract.strip())
    narrative = _extraction_narrative_text(rec.get("extraction_path"), repo_root)
    if narrative:
        parts.append(narrative)
    return " ".join(parts)


def read_embeddings(repo_root: Path) -> dict[str, dict]:
    """`{evidence_id: record}` from `data/papers/embeddings.jsonl`, or `{}` if absent. A
    corrupt line is skipped, never fatal (mirrors `registry.py Registry._load`)."""
    path = embeddings_path(repo_root)
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            eid = rec.get("evidence_id")
            if eid:
                out[eid] = rec
    return out


def write_embeddings(repo_root: Path, records: dict[str, dict]) -> None:
    path = embeddings_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for eid in sorted(records):
            fh.write(json.dumps(records[eid], ensure_ascii=False, sort_keys=False))
            fh.write("\n")
    tmp.replace(path)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity — no numpy needed at personal-library scale."""
    if len(a) != len(b):
        raise ValueError(f"vector dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def cmd_index(args) -> int:
    repo_root = Path(args.repo).expanduser().resolve()
    registry = Registry(repo_root)
    existing = read_embeddings(repo_root)

    candidates = [
        rec for rec in registry.records.values()
        if rec.get("metadata_status") != "pending"
    ]
    candidates.sort(key=lambda r: r.get("evidence_id") or "")

    to_embed = []
    skipped_current = 0
    for rec in candidates:
        eid = rec["evidence_id"]
        cur = existing.get(eid)
        if not args.force and cur is not None and cur.get("model") == args.model:
            skipped_current += 1
            continue
        to_embed.append(rec)
    if args.limit is not None:
        to_embed = to_embed[: args.limit]

    if not to_embed:
        emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "index",
              "repo": str(repo_root), "model": args.model, "embedded": 0,
              "skipped_current": skipped_current, "total_candidates": len(candidates)})
        return 0

    model = _load_sentence_transformer(args.model)
    texts = [embedding_text(rec, repo_root) for rec in to_embed]
    vectors = model.encode(texts, show_progress_bar=False)

    now = utcnow()
    with advisory_lock(repo_root, "embeddings"):
        current = read_embeddings(repo_root)
        for rec, vector in zip(to_embed, vectors):
            eid = rec["evidence_id"]
            vec_list = [float(x) for x in vector]
            current[eid] = {
                "schema_version": SCHEMA_VERSION,
                "evidence_id": eid,
                "model": args.model,
                "dim": len(vec_list),
                "vector": vec_list,
                "updated_at": now,
            }
        write_embeddings(repo_root, current)

    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "index",
          "repo": str(repo_root), "model": args.model, "embedded": len(to_embed),
          "skipped_current": skipped_current, "total_candidates": len(candidates)})
    return 0


def cmd_similar(args) -> int:
    repo_root = Path(args.repo).expanduser().resolve()
    path = embeddings_path(repo_root)
    if not path.exists():
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "similar",
              "error": f"no embeddings file at {path} — run `embeddings.py index` first"})
        return 1

    all_embeddings = read_embeddings(repo_root)
    target = all_embeddings.get(args.evidence_id)
    if target is None:
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "similar",
              "error": f"no embedding for evidence_id {args.evidence_id!r} — run "
                       f"`embeddings.py index` first"})
        return 1

    registry = Registry(repo_root)
    target_vector = target["vector"]
    scored = []
    for eid, rec in all_embeddings.items():
        if eid == args.evidence_id:
            continue
        try:
            score = cosine_similarity(target_vector, rec["vector"])
        except ValueError:
            continue
        scored.append((score, eid))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    top = scored[: args.k]

    results = []
    for score, eid in top:
        reg_rec = registry.records.get(eid) or {}
        results.append({
            "evidence_id": eid,
            "title": reg_rec.get("title"),
            "score": score,
        })

    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "similar",
          "repo": str(repo_root), "evidence_id": args.evidence_id, "k": args.k,
          "results": results})
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="embeddings.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("index", help="(re)embed registry records into embeddings.jsonl")
    s.add_argument("--repo", required=True)
    s.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"sentence-transformers model name (default: {DEFAULT_MODEL})")
    s.add_argument("--limit", type=int, help="only (re)embed the first N missing/stale records")
    s.add_argument("--force", action="store_true",
                   help="re-embed even records whose cached model already matches --model")
    s.set_defaults(func=cmd_index)

    s = sub.add_parser("similar", help="top-k nearest neighbours by cosine similarity")
    s.add_argument("--repo", required=True)
    s.add_argument("--evidence-id", dest="evidence_id", required=True)
    s.add_argument("--k", type=int, default=5)
    s.set_defaults(func=cmd_similar)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
