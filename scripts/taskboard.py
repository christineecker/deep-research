#!/usr/bin/env python3
"""taskboard.py — taskboard.jsonl state machine: `TaskBoard`, task id grammar, the status
transition graph, input hashing, and the `task` CLI subcommand implementations.

Split out of `corpus.py` (SIMPLIFICATION_PLAN.md Phase C). `corpus.py` remains the CLI
facade and the only entry point that writes `taskboard.jsonl`; it imports the command
functions below and keeps the argparse subparser wiring for `task ...` itself, so
`tests/test_docs.py`'s per-file static parse of corpus.py's CLI surface still sees it.

Enforcement point for `references/schema.md` §2 (taskboard record).

Files this module owns inside a run directory
  taskboard.jsonl   append-only log; last record per task_id wins
  .locks/taskboard.lock  advisory flock file (parallel subagents)
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import now_iso, read_json  # noqa: E402  (sibling module, stdlib-only)

SCHEMA_VERSION = 1

# --------------------------------------------------------------------------- enums

STAGES = (
    "protocol", "search", "screen", "adjudicate", "retrieve", "extract",
    "appraise", "synthesize", "digest", "verify", "report", "okf",
)
TASK_STATUSES = ("pending", "active", "completed", "blocked", "failed", "cancelled")
KEY_KINDS = ("pmid", "doi", "pmcid", "query", "url", "slug", "batch")

# schema.md §2 status transition graph
TRANSITIONS = {
    None:        {"pending"},
    "pending":   {"active", "cancelled"},
    "active":    {"completed", "failed", "blocked", "cancelled"},
    "failed":    {"pending", "active", "cancelled"},
    "blocked":   {"pending", "active"},          # re-openable by a resume
    "completed": set(),                          # terminal unless inputs_hash changes
    "cancelled": {"pending"},
}

TASK_ID_RE = re.compile(
    r"^(?P<stage>%s):(?P<kind>%s):(?P<key>[A-Za-z0-9._~-]+)$"
    % ("|".join(STAGES), "|".join(KEY_KINDS))
)


# --------------------------------------------------------------------------- errors


class UserError(Exception):
    """Operator-facing error; exit code 2."""


class StateError(Exception):
    """Illegal state transition / immutability violation; exit code 3."""


# ----------------------------------------------------------------------- utilities


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(obj) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def warn(msg: str) -> None:
    print(f"warn: {msg}", file=sys.stderr)


def read_jsonl(path: Path, *, strict: bool = False) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for n, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                msg = f"{path}:{n}: malformed JSONL ({exc})"
                if strict:
                    raise UserError(msg)
                warn(msg + " — line skipped")
                continue
            if not isinstance(obj, dict):
                warn(f"{path}:{n}: not a JSON object — line skipped")
                continue
            out.append(obj)
    return out


def append_line(path: Path, obj: dict) -> None:
    """Append exactly one complete JSONL line. Caller must hold the advisory lock."""
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    line = canonical_json(obj) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


# ------------------------------------------------------------------------ taskboard


class TaskBoard:
    """Append-only taskboard.jsonl; state = last record per task_id."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.path = run_dir / "taskboard.jsonl"

    def state(self) -> dict[str, dict]:
        state: dict[str, dict] = {}
        for obj in read_jsonl(self.path):
            tid = obj.get("task_id")
            if not tid:
                warn(f"{self.path}: record without task_id — skipped")
                continue
            state[tid] = obj
        return state

    def history(self) -> list[dict]:
        return read_jsonl(self.path)

    def get(self, task_id: str) -> dict | None:
        return self.state().get(task_id)

    def write(self, rec: dict) -> None:
        append_line(self.path, rec)


def validate_task_id(task_id: str) -> tuple[str, str, str]:
    m = TASK_ID_RE.match(task_id or "")
    if not m:
        raise UserError(
            f"invalid task_id {task_id!r}; grammar: <stage>:<key-kind>:<key> "
            f"(schema.md §2). stages={'|'.join(STAGES)} kinds={'|'.join(KEY_KINDS)}"
        )
    return m.group("stage"), m.group("kind"), m.group("key")


def check_transition(prev: dict | None, new_status: str, *, inputs_hash: str | None) -> None:
    old_status = prev.get("status") if prev else None
    if old_status == "completed":
        if inputs_hash is None or inputs_hash == prev.get("inputs_hash"):
            raise StateError(
                f"{prev['task_id']}: completed outputs are immutable "
                f"(inputs_hash {prev.get('inputs_hash')} unchanged); "
                "supply --inputs/--inputs-file with different inputs to invalidate"
            )
        return  # inputs changed -> reopening permitted
    allowed = TRANSITIONS.get(old_status, set())
    if new_status not in allowed:
        raise StateError(
            f"illegal transition {old_status or '<new>'} -> {new_status} "
            f"(allowed: {sorted(allowed) or 'none'})"
        )


def compute_inputs_hash(args, prev: dict | None) -> str | None:
    payload = None
    if getattr(args, "inputs_file", None):
        payload = read_json(Path(args.inputs_file))
    elif getattr(args, "inputs", None):
        payload = json.loads(args.inputs)
    if payload is None:
        return None
    return sha256_of(payload)


def make_task_record(*, task_id, stage, status, inputs_hash, attempts, worker,
                     output_path, error, created_at, summary=None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "stage": stage,
        "status": status,
        "inputs_hash": inputs_hash,
        "attempts": attempts,
        "worker": worker,
        "output_path": output_path,
        "error": error,
        "summary": summary,
        "created_at": created_at,
        "updated_at": now_iso(),
    }


# --------------------------------------------------------------------- task CLI


def _task_transition(args, new_status: str, *, worker=None, output_path=None,
                     error=None, summary=None, bump_attempts=False) -> dict:
    from corpus import advisory_lock  # local import: avoids a corpus<->taskboard cycle
    run_dir = Path(args.run_dir)
    task_id = args.task_id
    stage, _kind, _key = validate_task_id(task_id)
    board = TaskBoard(run_dir)
    with advisory_lock(run_dir, "taskboard"):
        prev = board.get(task_id)
        inputs_hash = compute_inputs_hash(args, prev)
        check_transition(prev, new_status, inputs_hash=inputs_hash)
        created_at = prev["created_at"] if prev else now_iso()
        attempts = int(prev.get("attempts") or 0) if prev else 0
        if bump_attempts:
            attempts += 1
        attempts = max(attempts, 1) if new_status != "pending" or prev else attempts
        rec = make_task_record(
            task_id=task_id,
            stage=stage,
            status=new_status,
            inputs_hash=inputs_hash or (prev.get("inputs_hash") if prev else sha256_of({"task_id": task_id})),
            attempts=attempts,
            worker=worker if worker is not None else (prev.get("worker") if prev else None),
            output_path=output_path if output_path is not None else (
                prev.get("output_path") if prev else None),
            error=error,
            summary=summary if summary is not None else (
                prev.get("summary") if prev else None),
            created_at=created_at,
        )
        board.write(rec)
    return rec


def cmd_task_create(args) -> int:
    from corpus import advisory_lock
    run_dir = Path(args.run_dir)
    stage, _k, _key = validate_task_id(args.task_id)
    board = TaskBoard(run_dir)
    with advisory_lock(run_dir, "taskboard"):
        prev = board.get(args.task_id)
        inputs_hash = compute_inputs_hash(args, prev) or sha256_of({"task_id": args.task_id})
        if prev is not None:
            if prev.get("inputs_hash") == inputs_hash:
                print(json.dumps({"created": False, "reason": "task already exists",
                                  "task": prev}, indent=2))
                return 0
            check_transition(prev, "pending", inputs_hash=inputs_hash)
        rec = make_task_record(
            task_id=args.task_id, stage=stage, status="pending", inputs_hash=inputs_hash,
            attempts=0, worker=None, output_path=args.output_path, error=None,
            created_at=prev["created_at"] if prev else now_iso(),
        )
        board.write(rec)
    print(json.dumps({"created": True, "task": rec}, indent=2))
    return 0


def cmd_task_claim(args) -> int:
    from corpus import advisory_lock
    run_dir = Path(args.run_dir)
    stage, _k, _key = validate_task_id(args.task_id)
    board = TaskBoard(run_dir)
    with advisory_lock(run_dir, "taskboard"):
        prev = board.get(args.task_id)
        inputs_hash = compute_inputs_hash(args, prev)
        if prev is None:
            # auto-create then claim, so a coordinator never hand-writes the file
            inputs_hash = inputs_hash or sha256_of({"task_id": args.task_id})
            created_at = now_iso()
            attempts = 1
        else:
            check_transition(prev, "active", inputs_hash=inputs_hash)
            inputs_hash = inputs_hash or prev.get("inputs_hash")
            created_at = prev["created_at"]
            attempts = int(prev.get("attempts") or 0) + 1
        rec = make_task_record(
            task_id=args.task_id, stage=stage, status="active", inputs_hash=inputs_hash,
            attempts=attempts, worker=args.worker,
            output_path=args.output_path or (prev.get("output_path") if prev else None),
            error=None, created_at=created_at,
        )
        board.write(rec)
    print(json.dumps(rec, indent=2))
    return 0


def cmd_task_complete(args) -> int:
    rec = _task_transition(args, "completed", worker=args.worker,
                           output_path=args.output_path, error=None,
                           summary=getattr(args, "summary", None))
    print(json.dumps(rec, indent=2))
    return 0


def cmd_task_fail(args) -> int:
    rec = _task_transition(args, "failed", worker=args.worker,
                           output_path=args.output_path, error=args.error)
    print(json.dumps(rec, indent=2))
    return 0


def cmd_task_block(args) -> int:
    rec = _task_transition(args, "blocked", worker=args.worker,
                           output_path=args.output_path, error=args.error)
    print(json.dumps(rec, indent=2))
    return 0


def cmd_task_cancel(args) -> int:
    rec = _task_transition(args, "cancelled", worker=args.worker,
                           error=args.error or "cancelled by guard/budget")
    print(json.dumps(rec, indent=2))
    return 0


def cmd_task_reopen(args) -> int:
    """failed|blocked|cancelled -> pending (retry). Completed needs changed inputs."""
    rec = _task_transition(args, "pending", worker=None, error=None)
    print(json.dumps(rec, indent=2))
    return 0


def _task_rows(args, statuses: tuple[str, ...] | None):
    board = TaskBoard(Path(args.run_dir))
    rows = list(board.state().values())
    if getattr(args, "stage", None):
        rows = [r for r in rows if r.get("stage") == args.stage]
    if statuses:
        rows = [r for r in rows if r.get("status") in statuses]
    rows.sort(key=lambda r: (r.get("created_at") or "", r.get("task_id") or ""))
    return rows


def cmd_task_list(args) -> int:
    statuses = tuple(args.status) if args.status else None
    rows = _task_rows(args, statuses)
    if args.format == "json":
        print(json.dumps(rows, indent=2))
    elif args.format == "ids":
        for r in rows:
            print(r["task_id"])
    else:
        print(f"{'task_id':<44} {'status':<10} {'att':>3} {'worker':<14} output_path")
        for r in rows:
            print(f"{r['task_id']:<44} {r['status']:<10} {r.get('attempts', 0):>3} "
                  f"{str(r.get('worker') or '-'):<14} {r.get('output_path') or '-'}")
            if r.get("summary"):
                print(f"{'':<44} └─ {r['summary']}")
            if r.get("error"):
                print(f"{'':<44} !! {r['error']}")
    return 0


def cmd_task_next(args) -> int:
    rows = _task_rows(args, ("pending", "failed"))
    rows = rows[: args.limit]
    if args.format == "json":
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(r["task_id"])
    return 0


def cmd_task_show(args) -> int:
    board = TaskBoard(Path(args.run_dir))
    rec = board.get(args.task_id)
    if rec is None:
        raise UserError(f"no such task: {args.task_id}")
    out = {"current": rec}
    if args.history:
        out["history"] = [h for h in board.history() if h.get("task_id") == args.task_id]
    print(json.dumps(out, indent=2))
    return 0


def cmd_task_stats(args) -> int:
    board = TaskBoard(Path(args.run_dir))
    rows = list(board.state().values())
    stats: dict[str, dict[str, int]] = {}
    for r in rows:
        stats.setdefault(r.get("stage", "?"), {})
        s = stats[r["stage"]]
        s[r["status"]] = s.get(r["status"], 0) + 1
    print(json.dumps({"tasks": len(rows), "by_stage": stats}, indent=2))
    return 0

