#!/usr/bin/env python3
"""watch.py — read-only run monitor TUI for a deep-research run directory.

The entire run state already lives on disk. This script is a **strictly read-only
poller** over it:

  * it NEVER writes anything into a run directory (no mkdir, no touch, no temp files),
  * it never takes the taskboard advisory lock (`corpus.py` remains the sole writer of
    `taskboard.jsonl`),
  * it never shells out to `corpus.py` / `fulltext.py` / any other script,
  * it never touches the network.

A crashed, paused or finished run therefore attaches exactly like a live one, and two
watchers on the same run cannot interfere with each other or with the coordinator.

Why it exists: subagents deliberately return only <=200-character receipts
(`references/schema.md` S4/§1) so the coordinator's context stays small over a long run.
That means the coordinator can no longer see per-task detail. This is where it becomes
visible again — especially a hung subagent, which is otherwise invisible.

Files read (all optional; a missing file degrades a panel, never crashes the program):

    config.json                          profile/scope/rigor/gates/filters/budgets
    taskboard.jsonl                      append-only; tailed by byte offset,
                                         state resolved last-record-wins (schema §2)
    corpus.jsonl                         rewritten atomically via os.replace; re-read
                                         wholly on (mtime,size) change, torn reads are
                                         tolerated by keeping the previous snapshot
    engine.log                           "<iso> <tool> <message>" lines, tailed
    missing.md                           quarantined records ("- evidence_id: ..." blocks)
    workspace/retrieve/mcp-tasks.jsonl   needs_mcp coordinator handoffs (fulltext.py)
    workspace/retrieve/<stem>.json       per-record ladder state; last completed rung
    outputs/verification.json            verifier checks (schema §9)
    workspace/{extractions,appraisals,screening}/...  read lazily, only to reconstruct a
                                         receipt-style one-line summary for the feed

Environment: python3 3.14, stdlib only (curses). No third-party imports, no pip.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
TOOL = "watch.py"

# The 8 pipeline stages of SKILL.md. Taskboard stages that are sub-steps of one of
# them are folded into their parent so the pipeline panel always has 8 rows.
PIPELINE = [
    ("protocol", "protocol", ()),
    ("search", "search", ()),
    ("screen", "screen", ("adjudicate",)),
    ("retrieve", "retrieve", ()),
    ("extract", "extract", ()),
    ("appraise", "appraise", ()),
    ("synthesize", "synthesize", ()),
    ("verify", "verify", ("report", "okf")),
]
STAGE_PARENT = {}
for _name, _label, _extra in PIPELINE:
    STAGE_PARENT[_name] = _name
    for _e in _extra:
        STAGE_PARENT[_e] = _name

TASK_STATUSES = ("pending", "active", "completed", "blocked", "failed", "cancelled")

TIER_LABEL = {
    0: "local library",
    1: "PMC MCP full text",
    2: "PMC PDF",
    3: "Europe PMC XML",
    4: "Unpaywall location",
    5: "OA PDF/HTML",
    6: "preprint twin",
    7: "quarantined",
}

DEFAULT_STALL = 300.0          # seconds; amber at 1x, red at 2x
DEFAULT_INTERVAL = 1.0         # stat-gated poll tick
FEED_CAP = 3000
LOG_CAP = 4000
SUMMARY_FILE_MAX = 2_000_000   # never slurp a pathological result file
BUDGET_WARN = 0.8              # fraction of a budget that turns the meter amber

PANELS = ["header", "pipeline", "workers", "funnel", "ladder", "feed", "alerts"]
PANEL_TITLE = {
    "header": "1 run",
    "pipeline": "2 pipeline",
    "workers": "3 workers",
    "funnel": "4 funnel",
    "ladder": "5 acquisition ladder",
    "feed": "6 activity",
    "alerts": "7 alerts",
}


# --------------------------------------------------------------------- utilities


def parse_iso(value) -> float | None:
    """ISO-8601 UTC 'Z' timestamp -> epoch seconds. Tolerant; None when unparseable."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def fmt_dur(seconds) -> str:
    if seconds is None:
        return "--"
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%dh%02dm" % (h, m)
    if m:
        return "%dm%02ds" % (m, s)
    return "%ds" % s


def parse_duration(value) -> float | None:
    """Budget durations: a number of seconds, or '90m' / '2h' / '3600s' / '1d'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", text)
    if not m:
        return None
    n = float(m.group(1))
    return n * {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}[m.group(2)]


def clip(text: str, width: int) -> str:
    """One printable line, no control characters, hard-clipped to width."""
    text = "".join(ch if ch.isprintable() or ch == " " else " " for ch in str(text))
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def sig_of(path: Path):
    """(mtime_ns, size, dev, ino) or None. The only syscall an idle tick makes."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_dev, st.st_ino)


def bar(fraction: float, width: int, fill: str = "#", empty: str = ".") -> str:
    width = max(0, width)
    fraction = 0.0 if fraction != fraction else min(1.0, max(0.0, fraction))
    n = int(round(fraction * width))
    return fill * n + empty * (width - n)


# ------------------------------------------------------------------- run reader


class RunReader:
    """Stat-gated, incremental, crash-tolerant reader for one run directory."""

    def __init__(self, run_dir: Path, stall_seconds: float = DEFAULT_STALL):
        self.run_dir = run_dir
        self.stall_seconds = stall_seconds

        self.config: dict = {}
        self.config_error: str | None = None
        self.tasks: dict[str, dict] = {}
        self.task_seq: dict[str, int] = {}
        self._seq = 0
        self.corpus: list[dict] = []
        self.corpus_stale: bool = False
        self.corpus_error: str | None = None
        self.mcp_tasks: list[dict] = []
        self.missing_blocks: list[dict] = []
        self.missing_text: str = ""
        self.verification: dict | None = None
        self.rungs: dict[str, dict] = {}
        self.log_lines: list[str] = []
        self.feed: list[dict] = []
        self.notes: list[str] = []

        self._sigs: dict[str, object] = {}
        self._tb_off = 0
        self._tb_buf = b""
        self._tb_ident = None
        self._log_off = 0
        self._log_buf = b""
        self._log_ident = None
        self._summary_cache: dict[str, tuple] = {}
        self.last_poll = 0.0
        self.first_poll_done = False

    # -- generic helpers ----------------------------------------------------

    def path(self, *parts) -> Path:
        return self.run_dir.joinpath(*parts)

    def _changed(self, key: str, path: Path) -> bool:
        sig = sig_of(path)
        if sig == self._sigs.get(key, "\0none"):
            return False
        self._sigs[key] = sig
        return True

    def _note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    # -- individual sources -------------------------------------------------

    def _read_config(self) -> None:
        p = self.path("config.json")
        try:
            self.config = json.loads(p.read_text(encoding="utf-8"))
            self.config_error = None
            if not isinstance(self.config, dict):
                raise ValueError("config.json is not a JSON object")
        except FileNotFoundError:
            self.config, self.config_error = {}, "config.json not found"
        except (OSError, ValueError) as exc:
            # keep whatever we had; a half-written config is a torn read
            self.config_error = "config.json unreadable: %s" % exc
            self._sigs["config"] = None

    def _reset_taskboard(self) -> None:
        self.tasks.clear()
        self.task_seq.clear()
        self.feed = [e for e in self.feed if e.get("kind") != "task"]
        self._tb_off = 0
        self._tb_buf = b""
        self._seq = 0

    def _read_taskboard(self, force: bool = False) -> None:
        p = self.path("taskboard.jsonl")
        try:
            st = p.stat()
        except OSError:
            if self.tasks:
                self._reset_taskboard()
            return
        ident = (st.st_dev, st.st_ino)
        if force or ident != self._tb_ident or st.st_size < self._tb_off:
            # new file, replaced file, or truncation: resolve from scratch
            self._reset_taskboard()
            self._tb_ident = ident
        if st.st_size == self._tb_off:
            return
        try:
            with open(p, "rb") as fh:
                fh.seek(self._tb_off)
                chunk = fh.read()
                self._tb_off = fh.tell()
        except OSError as exc:
            self._note("taskboard read error: %s" % exc)
            return
        data = self._tb_buf + chunk
        lines = data.split(b"\n")
        self._tb_buf = lines.pop()          # trailing partial line, retried next tick
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw.decode("utf-8", "replace"))
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(rec, dict) or not rec.get("task_id"):
                continue
            self._apply_task(rec)

    def _apply_task(self, rec: dict) -> None:
        tid = rec["task_id"]
        prev = self.tasks.get(tid)
        self.tasks[tid] = rec                # last record wins
        if tid not in self.task_seq:
            self._seq += 1
            self.task_seq[tid] = self._seq
        status = rec.get("status")
        prev_status = prev.get("status") if prev else None
        if status in ("completed", "failed", "blocked", "cancelled") and status != prev_status:
            self.feed.append({
                "ts": parse_iso(rec.get("updated_at")) or time.time(),
                "kind": "task",
                "status": status,
                "task_id": tid,
                "stage": rec.get("stage"),
                "worker": rec.get("worker"),
                "text": None,                # filled lazily by receipt_summary()
                "rec": rec,
            })

    def _read_corpus(self) -> None:
        p = self.path("corpus.jsonl")
        try:
            raw = p.read_bytes()
        except FileNotFoundError:
            self.corpus, self.corpus_stale, self.corpus_error = [], False, None
            return
        except OSError as exc:
            self.corpus_stale, self.corpus_error = True, "corpus read error: %s" % exc
            self._sigs["corpus"] = None
            return
        recs, bad = [], 0
        for line in raw.split(b"\n"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line.decode("utf-8", "replace"))
            except (ValueError, UnicodeDecodeError):
                bad += 1
                continue
            if isinstance(obj, dict) and obj.get("evidence_id"):
                recs.append(obj)
        torn = bad > 0 or (self.corpus and not recs)
        if torn:
            # keep the previous good snapshot and retry on the next tick
            self.corpus_stale = True
            self.corpus_error = "corpus.jsonl torn/partial (%d bad line(s)); showing last good snapshot" % bad
            self._sigs["corpus"] = None
            return
        self.corpus = recs
        self.corpus_stale = False
        self.corpus_error = None

    def _read_mcp(self) -> None:
        p = self.path("workspace", "retrieve", "mcp-tasks.jsonl")
        tasks = []
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            self.mcp_tasks = []
            return
        except OSError:
            self._sigs["mcp"] = None
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                tasks.append(obj)
        self.mcp_tasks = tasks

    def _read_missing(self) -> None:
        p = self.path("missing.md")
        try:
            self.missing_text = p.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            self.missing_text, self.missing_blocks = "", []
            return
        except OSError:
            self._sigs["missing"] = None
            return
        blocks, cur = [], None
        for line in self.missing_text.splitlines():
            if line.startswith("## "):
                cur = {"title": line[3:].strip(), "evidence_id": None, "pmid": None}
                blocks.append(cur)
            elif cur is not None and line.startswith("- evidence_id:"):
                cur["evidence_id"] = line.split(":", 1)[1].strip()
            elif cur is not None and line.startswith("- PMID:"):
                cur["pmid"] = line.split(":", 1)[1].strip()
        self.missing_blocks = [b for b in blocks if b.get("evidence_id")] or blocks

    def _read_verification(self) -> None:
        p = self.path("outputs", "verification.json")
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.verification = None
            return
        except (OSError, ValueError):
            self._sigs["verify"] = None      # torn write; retry next tick
            return
        if isinstance(obj, dict):
            self.verification = obj

    def _read_rungs(self) -> None:
        d = self.path("workspace", "retrieve")
        if not d.is_dir():
            self.rungs = {}
            return
        out = {}
        try:
            entries = sorted(d.glob("*.json"))
        except OSError:
            return
        for f in entries:
            try:
                st = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(st, dict):
                continue
            rungs = st.get("rungs") or {}
            best = None
            for key, entry in rungs.items():
                try:
                    tier = int(key)
                except (TypeError, ValueError):
                    continue
                if not isinstance(entry, dict):
                    continue
                if best is None or tier > best[0]:
                    best = (tier, entry)
            eid = st.get("evidence_id") or f.stem
            out[eid] = {
                "tier": best[0] if best else None,
                "status": (best[1].get("status") if best else None),
                "detail": (best[1].get("detail") if best else None),
                "at": (best[1].get("at") if best else None),
                "attempted": sorted(int(k) for k in rungs if str(k).isdigit()),
            }
        self.rungs = out

    def _read_log(self, force: bool = False) -> None:
        p = self.path("engine.log")
        try:
            st = p.stat()
        except OSError:
            return
        ident = (st.st_dev, st.st_ino)
        if force or ident != self._log_ident or st.st_size < self._log_off:
            self._log_ident = ident
            self._log_off = 0
            self._log_buf = b""
            self.log_lines = []
            self.feed = [e for e in self.feed if e.get("kind") != "log"]
        if st.st_size == self._log_off:
            return
        try:
            with open(p, "rb") as fh:
                fh.seek(self._log_off)
                chunk = fh.read()
                self._log_off = fh.tell()
        except OSError:
            return
        data = self._log_buf + chunk
        parts = data.split(b"\n")
        self._log_buf = parts.pop()
        for raw in parts:
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            self.log_lines.append(line)
            head = line.split(" ", 1)
            ts = parse_iso(head[0])
            self.feed.append({
                "ts": ts if ts is not None else time.time(),
                "kind": "log",
                "status": None,
                "task_id": None,
                "stage": None,
                "worker": None,
                "text": line if ts is None else head[1] if len(head) > 1 else "",
                "rec": None,
            })
        if len(self.log_lines) > LOG_CAP:
            del self.log_lines[: len(self.log_lines) - LOG_CAP]

    # -- poll ---------------------------------------------------------------

    def poll(self, force: bool = False) -> bool:
        """One stat-gated tick. Returns True when anything changed."""
        changed = False
        self.notes = []
        if self._changed("config", self.path("config.json")) or force:
            self._read_config()
            changed = True
        if self._changed("taskboard", self.path("taskboard.jsonl")) or force:
            self._read_taskboard(force=force)
            changed = True
        if self._changed("corpus", self.path("corpus.jsonl")) or force:
            self._read_corpus()
            changed = True
        if self._changed("mcp", self.path("workspace", "retrieve", "mcp-tasks.jsonl")) or force:
            self._read_mcp()
            changed = True
        if self._changed("missing", self.path("missing.md")) or force:
            self._read_missing()
            changed = True
        if self._changed("verify", self.path("outputs", "verification.json")) or force:
            self._read_verification()
            changed = True
        if self._changed("rungdir", self.path("workspace", "retrieve")) or force:
            self._read_rungs()
            changed = True
        if self._changed("log", self.path("engine.log")) or force:
            self._read_log(force=force)
            changed = True
        if changed:
            self.feed.sort(key=lambda e: e["ts"])
            if len(self.feed) > FEED_CAP:
                del self.feed[: len(self.feed) - FEED_CAP]
        self.last_poll = time.time()
        self.first_poll_done = True
        return changed

    # -- receipts -----------------------------------------------------------

    def receipt_summary(self, rec: dict) -> str:
        """The <=200-char receipt-style line for a task, reconstructed read-only.

        `corpus.py` does not carry the subagent's `summary` on the taskboard record, so
        when it is absent we derive an equivalent single line from the result file the
        subagent wrote. This is exactly the detail the coordinator no longer sees.
        """
        summary = rec.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()[:200]
        if rec.get("status") in ("failed", "blocked", "cancelled"):
            err = rec.get("error")
            if isinstance(err, str) and err.strip():
                return err.strip().splitlines()[0][:200]
            return "(no diagnostic recorded)"
        out = rec.get("output_path")
        if not isinstance(out, str) or not out:
            return "(no output_path)"
        target = self.path(out)
        text = self._summarize_output(target)
        return text or "-> %s" % out

    def _summarize_output(self, target: Path) -> str | None:
        try:
            st = target.stat()
        except OSError:
            return None
        if st.st_size > SUMMARY_FILE_MAX:
            return None
        key = str(target)
        cached = self._summary_cache.get(key)
        sig = (st.st_mtime_ns, st.st_size)
        if cached and cached[0] == sig:
            return cached[1]
        if target.is_dir():
            try:
                n = sum(1 for _ in target.iterdir())
            except OSError:
                n = 0
            text = "%d result file(s) in %s/" % (n, target.name)   # R2: batch -> directory
            self._summary_cache[key] = (sig, text)
            return text
        try:
            obj = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        text = summarize_record(obj)
        self._summary_cache[key] = (sig, text)
        return text


def summarize_record(obj) -> str | None:
    """Best-effort one-line receipt for any workspace result record."""
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("summary"), str):
        return obj["summary"][:200]
    if "decision" in obj and "reason" in obj:          # screening verdict, schema §5
        bits = "screen %s" % obj.get("decision")
        if obj.get("criterion_failed"):
            bits += " (%s)" % obj["criterion_failed"]
        return ("%s: %s" % (bits, obj.get("reason") or ""))[:200]
    if "final_decision" in obj:                        # adjudication, schema §6
        return ("adjudicated %s: %s" % (obj["final_decision"], obj.get("rationale") or ""))[:200]
    if "outcomes" in obj and "design" in obj:          # extraction, schema §7
        outs = obj.get("outcomes") or []
        head = "%s, n=%s, %d outcome(s)" % (
            obj.get("design") or "design?", obj.get("n_total"), len(outs))
        if outs and isinstance(outs[0], dict):
            head += "; %s %s" % (outs[0].get("name") or "?", outs[0].get("direction") or "")
        if obj.get("evidence_basis") == "abstract_only":
            head += " [abstract-only]"
        return head[:200]
    if "overall_judgement" in obj:                     # appraisal, schema §8
        grade = (obj.get("grade") or {}).get("certainty")
        head = "%s -> %s" % (obj.get("tool") or "?", obj.get("overall_judgement"))
        if grade:
            head += "; GRADE %s" % grade
        if obj.get("evidence_basis") == "abstract_only":
            head += " [abstract-only]"
        return head[:200]
    if "checks" in obj:                                # verifier, schema §9
        checks = obj.get("checks") or []
        fails = sum(1 for c in checks if isinstance(c, dict) and c.get("status") == "fail")
        return "verifier: %d check(s), %d fail" % (len(checks), fails)
    if "query_string" in obj:                          # search result, schema §3
        return ("query %s: %s hits" % (obj.get("query_id"), obj.get("count")))[:200]
    return None


# ------------------------------------------------------------------- derivation


def stage_counts(tasks: dict) -> dict:
    out = {name: {s: 0 for s in TASK_STATUSES} for name, _l, _e in PIPELINE}
    other = {s: 0 for s in TASK_STATUSES}
    for rec in tasks.values():
        parent = STAGE_PARENT.get(rec.get("stage"))
        status = rec.get("status")
        if status not in out.get(parent, other):
            continue
        (out[parent] if parent in out else other)[status] += 1
    return out


def funnel_of(corpus: list[dict]) -> dict:
    """PRISMA-style funnel, computed here (never by shelling out to corpus.py)."""
    dupes = sum(len(r.get("merged_from") or []) for r in corpus)
    screened = [r for r in corpus if r.get("screening")]
    included_screen = [r for r in screened
                       if (r.get("screening") or {}).get("decision") == "include"]
    excluded = [r for r in screened
                if (r.get("screening") or {}).get("decision") == "exclude"]
    unclear = [r for r in screened
               if (r.get("screening") or {}).get("decision") == "unclear"]

    def ft(r):
        return r.get("fulltext") or {}

    missing = [r for r in included_screen if ft(r).get("status") == "missing"]
    quarantined = [r for r in missing if ft(r).get("access_route")]      # R8
    not_attempted = [r for r in missing if not ft(r).get("access_route")]
    obtained = [r for r in included_screen if ft(r).get("status") not in (None, "missing")]
    fulltext = [r for r in obtained if ft(r).get("status") == "fulltext"]
    abstract_only = [r for r in obtained if ft(r).get("status") == "abstract_only"]
    return {
        "identified": len(corpus) + dupes,
        "duplicates_removed": dupes,
        "deduped": len(corpus),
        "screened": len(screened),
        "excluded": len(excluded),
        "unclear": len(unclear),
        "included": len(included_screen),
        "obtained": len(obtained),
        "fulltext": len(fulltext),
        "abstract_only": len(abstract_only),
        "quarantined": len(quarantined),
        "retrieval_not_yet_attempted": len(not_attempted),
        "with_extraction": sum(1 for r in obtained if r.get("extraction_path")),
        "with_appraisal": sum(1 for r in obtained if r.get("appraisal_path")),
        "preprints": sum(1 for r in obtained if r.get("is_preprint")),
        "retracted": sum(1 for r in obtained if r.get("retraction_status") not in (None, "none")),
    }


def ladder_of(corpus: list[dict]) -> dict:
    """Histogram over fulltext.source_tier, keeping R8 visually distinct from tier 7."""
    tiers = {t: 0 for t in range(8)}
    not_attempted = 0
    unknown = 0
    for r in corpus:
        ft = r.get("fulltext") or {}
        tier, route = ft.get("source_tier"), ft.get("access_route")
        if tier is None:
            # R8: source_tier is null until stage 4 has actually attempted retrieval
            if route:
                unknown += 1
            else:
                not_attempted += 1
            continue
        if isinstance(tier, int) and 0 <= tier <= 7:
            tiers[tier] += 1
        else:
            unknown += 1
    return {"tiers": tiers, "not_attempted": not_attempted, "unknown": unknown}


def active_workers(tasks: dict, now: float, stall: float) -> list[dict]:
    rows = []
    for rec in tasks.values():
        if rec.get("status") != "active":
            continue
        upd = parse_iso(rec.get("updated_at"))
        idle = (now - upd) if upd is not None else None
        level = "ok"
        if idle is not None:
            if idle >= 2 * stall:
                level = "red"
            elif idle >= stall:
                level = "amber"
        rows.append({
            "worker": rec.get("worker") or "-",
            "task_id": rec.get("task_id"),
            "stage": rec.get("stage"),
            "attempts": rec.get("attempts"),
            "updated_at": rec.get("updated_at"),
            "idle_seconds": idle,
            "stall_level": level,
            "output_path": rec.get("output_path"),
        })
    rows.sort(key=lambda r: (-(r["idle_seconds"] or 0), r["task_id"] or ""))
    return rows


def run_started_at(reader: RunReader) -> float | None:
    cfg = reader.config
    for key in ("started_at", "created_at", "run_started_at"):
        ts = parse_iso(cfg.get(key))
        if ts is not None:
            return ts
    stamps = [parse_iso(r.get("created_at")) for r in reader.tasks.values()]
    stamps = [s for s in stamps if s is not None]
    if stamps:
        return min(stamps)
    sig = sig_of(reader.path("config.json"))
    if sig:
        return sig[0] / 1e9
    return None


def budgets_of(reader: RunReader, funnel: dict, now: float) -> list[dict]:
    cfg = reader.config
    budgets = cfg.get("budgets") if isinstance(cfg.get("budgets"), dict) else cfg
    started = run_started_at(reader)
    elapsed = (now - started) if started else None

    subagent_workers = {
        r.get("worker") for r in reader.tasks.values()
        if r.get("worker") and r.get("worker") != "main"
    }
    out = []

    def add(name, used, cap, fmt=str):
        if cap in (None, 0):
            out.append({"name": name, "used": used, "cap": None, "fraction": None,
                        "text": "%s / --" % fmt(used)})
            return
        frac = (used / cap) if cap else None
        out.append({"name": name, "used": used, "cap": cap, "fraction": frac,
                    "text": "%s / %s" % (fmt(used), fmt(cap))})

    add("articles", funnel["included"], budgets.get("max_articles"))
    add("subagents", len(subagent_workers), budgets.get("max_subagents"))
    wall_cap = parse_duration(budgets.get("max_wall_time"))
    add("wall", elapsed if elapsed is not None else 0, wall_cap, fmt_dur)
    ftf = sum(1 for r in reader.corpus
              if (r.get("fulltext") or {}).get("status") == "missing"
              and (r.get("fulltext") or {}).get("access_route"))
    add("ft-fail", ftf, budgets.get("max_fulltext_failures"))
    return out


def alerts_of(reader: RunReader, funnel: dict, workers: list[dict],
              budgets: list[dict]) -> list[dict]:
    alerts: list[dict] = []

    pending_mcp = [t for t in reader.mcp_tasks
                   if t.get("status") == "needs_mcp"
                   and not (t.get("result_path")
                            and reader.path(t["result_path"]).exists())]
    if pending_mcp:
        alerts.append({
            "level": "amber", "kind": "mcp",
            "text": "%d MCP full-text handoff(s) awaiting the coordinator "
                    "(press m)" % len(pending_mcp),
        })

    for row in workers:
        if row["stall_level"] == "red":
            alerts.append({"level": "red", "kind": "stall",
                           "text": "STALLED %s: %s idle %s (>2x threshold)"
                                   % (row["worker"], row["task_id"],
                                      fmt_dur(row["idle_seconds"]))})
        elif row["stall_level"] == "amber":
            alerts.append({"level": "amber", "kind": "stall",
                           "text": "slow %s: %s idle %s"
                                   % (row["worker"], row["task_id"],
                                      fmt_dur(row["idle_seconds"]))})

    n_missing = len(reader.missing_blocks)
    if n_missing:
        alerts.append({"level": "amber", "kind": "quarantine",
                       "text": "%d quarantined full text(s) in missing.md — drop PDFs into "
                               "inbox/ and re-run (press x)" % n_missing})

    for b in budgets:
        if b["fraction"] is not None and b["fraction"] >= 1.0:
            alerts.append({"level": "red", "kind": "budget",
                           "text": "budget %s at cap: %s" % (b["name"], b["text"])})
        elif b["fraction"] is not None and b["fraction"] >= BUDGET_WARN:
            alerts.append({"level": "amber", "kind": "budget",
                           "text": "budget %s near cap: %s" % (b["name"], b["text"])})

    failed = [r for r in reader.tasks.values() if r.get("status") == "failed"]
    blocked = [r for r in reader.tasks.values() if r.get("status") == "blocked"]
    for rec in failed[:8]:
        alerts.append({"level": "red", "kind": "task",
                       "text": "failed %s: %s" % (rec.get("task_id"),
                                                  (rec.get("error") or "no diagnostic"))})
    if len(failed) > 8:
        alerts.append({"level": "red", "kind": "task",
                       "text": "... and %d more failed task(s)" % (len(failed) - 8)})
    for rec in blocked[:5]:
        alerts.append({"level": "amber", "kind": "task",
                       "text": "blocked %s: %s" % (rec.get("task_id"),
                                                   (rec.get("error") or "no diagnostic"))})

    if reader.verification:
        for chk in reader.verification.get("checks") or []:
            if not isinstance(chk, dict):
                continue
            if chk.get("status") == "fail":
                alerts.append({"level": "red", "kind": "verify",
                               "text": "verify FAIL %s: %s" % (chk.get("check_id"),
                                                               chk.get("detail"))})
            elif chk.get("status") == "warn":
                alerts.append({"level": "amber", "kind": "verify",
                               "text": "verify warn %s: %s" % (chk.get("check_id"),
                                                               chk.get("detail"))})

    if funnel["retrieval_not_yet_attempted"]:
        alerts.append({"level": "dim", "kind": "retrieval",
                       "text": "%d included record(s) have no retrieval attempt yet "
                               "(source_tier null, R8)"
                               % funnel["retrieval_not_yet_attempted"]})
    if reader.corpus_stale and reader.corpus_error:
        alerts.append({"level": "amber", "kind": "io", "text": reader.corpus_error})
    if reader.config_error:
        alerts.append({"level": "amber", "kind": "io", "text": reader.config_error})
    for note in reader.notes:
        alerts.append({"level": "amber", "kind": "io", "text": note})
    if not alerts:
        alerts.append({"level": "ok", "kind": "none", "text": "nothing needs the operator"})
    return alerts


def snapshot(reader: RunReader, now: float | None = None) -> dict:
    now = now or time.time()
    cfg = reader.config
    funnel = funnel_of(reader.corpus)
    ladder = ladder_of(reader.corpus)
    workers = active_workers(reader.tasks, now, reader.stall_seconds)
    budgets = budgets_of(reader, funnel, now)
    stages = stage_counts(reader.tasks)
    started = run_started_at(reader)
    totals = {s: 0 for s in TASK_STATUSES}
    for counts in stages.values():
        for s in TASK_STATUSES:
            totals[s] += counts[s]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.fromtimestamp(now, timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": TOOL,
        "run_dir": str(reader.run_dir),
        "run": {
            "slug": cfg.get("slug") or reader.run_dir.name,
            "question": cfg.get("question") or cfg.get("title"),
            "profile": cfg.get("profile"),
            "scope": cfg.get("scope"),
            "rigor": cfg.get("rigor"),
            "gates": cfg.get("gates"),
            "wiki": cfg.get("wiki") or cfg.get("wiki_root"),
            "started_at": (datetime.fromtimestamp(started, timezone.utc)
                           .strftime("%Y-%m-%dT%H:%M:%SZ") if started else None),
            "elapsed_seconds": int(now - started) if started else None,
            "config_error": reader.config_error,
        },
        "budgets": budgets,
        "stages": stages,
        "task_totals": totals,
        "workers": workers,
        "stall_seconds": reader.stall_seconds,
        "funnel": funnel,
        "ladder": ladder,
        "mcp_pending": [
            {"evidence_id": t.get("evidence_id"), "tool": t.get("tool"),
             "args": t.get("args"), "result_path": t.get("result_path")}
            for t in reader.mcp_tasks
            if t.get("status") == "needs_mcp"
            and not (t.get("result_path") and reader.path(t["result_path"]).exists())
        ],
        "quarantined": reader.missing_blocks,
        "verification": reader.verification,
        "alerts": alerts_of(reader, funnel, workers, budgets),
        "last_rungs": reader.rungs,
        "recent": [
            {"at": datetime.fromtimestamp(e["ts"], timezone.utc)
                           .strftime("%Y-%m-%dT%H:%M:%SZ"),
             "kind": e["kind"], "status": e["status"], "task_id": e["task_id"],
             "text": e["text"] if e["kind"] == "log"
                     else reader.receipt_summary(e["rec"])}
            for e in reader.feed[-40:]
        ],
        "corpus_stale": reader.corpus_stale,
    }


# ------------------------------------------------------------------ plain output


def render_text(reader: RunReader, width: int = 100) -> str:
    snap = snapshot(reader)
    run, out = snap["run"], []
    out.append("deep-research run monitor — %s" % snap["run_dir"])
    out.append("slug=%s profile=%s scope=%s rigor=%s gates=%s"
               % (run["slug"], run["profile"], run["scope"], run["rigor"], run["gates"]))
    if run["question"]:
        out.append("question: %s" % clip(run["question"], width - 10))
    out.append("elapsed %s   budgets: %s"
               % (fmt_dur(run["elapsed_seconds"]),
                  "  ".join("%s %s" % (b["name"], b["text"]) for b in snap["budgets"])))
    out.append("")
    out.append("PIPELINE   (pend/act/done/blk/fail)")
    for name, label, extra in PIPELINE:
        c = snap["stages"][name]
        total = sum(c.values())
        frac = (c["completed"] / total) if total else 0.0
        note = ("+" + ",".join(extra)) if extra else ""
        out.append("  %-11s [%s] %3d/%-3d  %d/%d/%d/%d/%d %s"
                   % (label, bar(frac, 20), c["completed"], total, c["pending"],
                      c["active"], c["completed"], c["blocked"], c["failed"], note))
    out.append("")
    out.append("WORKERS (stall threshold %s, red at 2x)" % fmt_dur(snap["stall_seconds"]))
    if not snap["workers"]:
        out.append("  no active tasks")
    for w in snap["workers"]:
        out.append("  %-14s %-40s idle %-8s %s"
                   % (w["worker"], w["task_id"], fmt_dur(w["idle_seconds"]),
                      {"ok": "", "amber": "<- SLOW", "red": "<- STALLED"}[w["stall_level"]]))
    out.append("")
    f = snap["funnel"]
    out.append("FUNNEL")
    out.append("  identified %d  deduped %d (-%d dup)  screened %d  included %d"
               % (f["identified"], f["deduped"], f["duplicates_removed"],
                  f["screened"], f["included"]))
    out.append("  full-text %d  abstract-only %d  quarantined %d  retrieval-not-attempted %d"
               % (f["fulltext"], f["abstract_only"], f["quarantined"],
                  f["retrieval_not_yet_attempted"]))
    out.append("  extracted %d  appraised %d  preprints %d  retracted/EoC %d"
               % (f["with_extraction"], f["with_appraisal"], f["preprints"], f["retracted"]))
    out.append("")
    lad = snap["ladder"]
    peak = max(list(lad["tiers"].values()) + [lad["not_attempted"], 1])
    out.append("ACQUISITION LADDER")
    for tier in range(8):
        n = lad["tiers"][tier]
        out.append("  t%d %-18s %-24s %d"
                   % (tier, TIER_LABEL[tier], bar(n / peak, 24, "#", " "), n))
    out.append("  -- %-18s %-24s %d"
               % ("not yet attempted", bar(lad["not_attempted"] / peak, 24, "-", " "),
                  lad["not_attempted"]))
    out.append("")
    out.append("ACTIVITY (newest last)")
    for e in snap["recent"][-15:]:
        tag = e["status"] or e["kind"]
        out.append("  %s %-9s %s" % (e["at"][11:19], tag,
                                     clip("%s %s" % (e["task_id"] or "", e["text"] or ""),
                                          width - 22)))
    out.append("")
    out.append("ALERTS")
    for a in snap["alerts"]:
        out.append("  [%-5s] %s" % (a["level"], clip(a["text"], width - 12)))
    return "\n".join(out)


# ------------------------------------------------------------------------ curses


class Tui:
    def __init__(self, reader: RunReader, interval: float, use_color: bool):
        self.reader = reader
        self.interval = interval
        self.use_color = use_color
        self.focus = 1                     # index into PANELS; 0 (header) never scrolls
        self.scroll = {name: 0 for name in PANELS}
        self.sel = {name: 0 for name in PANELS}
        self.autoscroll = True
        self.overlay: str | None = None    # None | 'mcp' | 'missing' | 'log' | 'detail'
        self.overlay_scroll = 0
        self.overlay_lines: list[tuple[str, str]] = []
        self.status = ""
        self.attrs: dict[str, int] = {}
        self.resized = False

    # -- colour -------------------------------------------------------------

    def init_colors(self) -> None:
        import curses
        plain = {k: curses.A_NORMAL for k in
                 ("norm", "dim", "ok", "warn", "err", "head", "accent")}
        plain["head"] = curses.A_BOLD
        plain["dim"] = curses.A_DIM
        plain["sel"] = curses.A_REVERSE
        plain["err"] = curses.A_BOLD
        self.attrs = plain
        if not self.use_color:
            return
        try:
            if not curses.has_colors():
                return
            curses.start_color()
            curses.use_default_colors()     # keep the terminal's own background
        except curses.error:
            return
        pairs = [("ok", curses.COLOR_GREEN), ("warn", curses.COLOR_YELLOW),
                 ("err", curses.COLOR_RED), ("head", curses.COLOR_CYAN),
                 ("accent", curses.COLOR_BLUE)]
        for idx, (name, color) in enumerate(pairs, start=1):
            try:
                curses.init_pair(idx, color, -1)
            except curses.error:
                continue
            self.attrs[name] = curses.color_pair(idx)
        self.attrs["head"] |= curses.A_BOLD
        self.attrs["err"] |= curses.A_BOLD

    def attr(self, key: str) -> int:
        return self.attrs.get(key, 0)

    # -- panel content ------------------------------------------------------

    def header_lines(self, snap, width) -> list[tuple[str, str]]:
        run = snap["run"]
        lines = [("%s   %s" % (run["slug"], run["question"] or ""), "head")]
        lines.append(("profile %s | scope %s | rigor %s | gates %s | elapsed %s"
                      % (run["profile"], run["scope"], run["rigor"], run["gates"],
                         fmt_dur(run["elapsed_seconds"])), "norm"))
        meters = []
        for b in snap["budgets"]:
            if b["fraction"] is None:
                meters.append("%s %s" % (b["name"], b["text"]))
            else:
                meters.append("%s [%s] %s" % (b["name"], bar(b["fraction"], 8), b["text"]))
        lines.append(("budget: " + "  ".join(meters),
                      "err" if any(b["fraction"] is not None and b["fraction"] >= 1.0
                                   for b in snap["budgets"])
                      else "warn" if any(b["fraction"] is not None
                                         and b["fraction"] >= BUDGET_WARN
                                         for b in snap["budgets"])
                      else "norm"))
        return lines

    def pipeline_lines(self, snap, width) -> list[tuple[str, str]]:
        bw = max(6, min(24, width - 46))
        lines = []
        for name, label, extra in PIPELINE:
            c = snap["stages"][name]
            total = sum(c.values())
            frac = (c["completed"] / total) if total else 0.0
            key = "dim" if total == 0 else ("err" if c["failed"] else
                                            "ok" if total and c["completed"] == total else
                                            "accent" if c["active"] else "norm")
            label_out = label + ("+" + "+".join(e[:3] for e in extra)
                                 if extra and width > 76 else "")
            lines.append(("%-14s [%s] %3d/%-3d  p%d a%d c%d b%d f%d"
                          % (clip(label_out, 14), bar(frac, bw), c["completed"], total,
                             c["pending"], c["active"], c["completed"],
                             c["blocked"], c["failed"]), key))
        return lines

    def workers_lines(self, snap, width) -> list[tuple[str, str]]:
        if not snap["workers"]:
            return [("no active tasks", "dim")]
        lines = []
        for w in snap["workers"]:
            key = {"ok": "norm", "amber": "warn", "red": "err"}[w["stall_level"]]
            flag = {"ok": "", "amber": "SLOW", "red": "STALLED"}[w["stall_level"]]
            lines.append(("%-12s %-38s idle %-8s try%s %s"
                          % (clip(w["worker"], 12), clip(w["task_id"] or "", 38),
                             fmt_dur(w["idle_seconds"]), w["attempts"], flag), key))
        return lines

    def funnel_lines(self, snap, width) -> list[tuple[str, str]]:
        f = snap["funnel"]
        rows = [
            ("identified", f["identified"], "norm"),
            ("deduped", f["deduped"], "norm"),
            ("screened", f["screened"], "norm"),
            ("included", f["included"], "accent"),
            ("full text", f["fulltext"], "ok"),
            ("abstract only", f["abstract_only"], "warn"),
            ("quarantined", f["quarantined"], "err" if f["quarantined"] else "dim"),
            ("not attempted", f["retrieval_not_yet_attempted"], "dim"),
        ]
        peak = max([n for _l, n, _k in rows] + [1])
        bw = max(4, min(30, width - 32))
        out = []
        for label, n, key in rows:
            out.append(("%-15s %5d %s" % (label, n, bar(n / peak, bw, "█", " ")), key))
        out.append(("extracted %d | appraised %d | preprints %d | retracted %d"
                    % (f["with_extraction"], f["with_appraisal"], f["preprints"],
                       f["retracted"]), "dim"))
        return out

    def ladder_lines(self, snap, width) -> list[tuple[str, str]]:
        lad = snap["ladder"]
        peak = max(list(lad["tiers"].values()) + [lad["not_attempted"], 1])
        bw = max(4, min(30, width - 34))
        out = []
        for tier in range(8):
            n = lad["tiers"][tier]
            key = "err" if tier == 7 and n else ("ok" if n and tier <= 3 else
                                                 "norm" if n else "dim")
            out.append(("t%d %-17s %4d %s"
                        % (tier, TIER_LABEL[tier], n, bar(n / peak, bw, "█", " ")), key))
        # R8: retrieval not yet attempted is NOT tier 7 — a different glyph, dim.
        out.append(("-- %-17s %4d %s"
                    % ("not yet attempted", lad["not_attempted"],
                       bar(lad["not_attempted"] / peak, bw, "░", " ")), "dim"))
        if lad["unknown"]:
            out.append(("?? out-of-range source_tier: %d" % lad["unknown"], "warn"))
        return out

    def feed_lines(self, snap, width) -> list[tuple[str, str]]:
        out = []
        for e in self.reader.feed:
            stamp = datetime.fromtimestamp(e["ts"], timezone.utc).strftime("%H:%M:%S")
            if e["kind"] == "log":
                out.append(("%s log       %s" % (stamp, e["text"] or ""), "dim"))
                continue
            key = {"completed": "ok", "failed": "err",
                   "blocked": "warn", "cancelled": "dim"}.get(e["status"], "norm")
            summary = self.reader.receipt_summary(e["rec"])
            out.append(("%s %-9s %s  %s" % (stamp, e["status"], e["task_id"], summary), key))
        if not out:
            out = [("no activity yet", "dim")]
        return out

    def alerts_lines(self, snap, width) -> list[tuple[str, str]]:
        keymap = {"red": "err", "amber": "warn", "ok": "ok", "dim": "dim"}
        return [(a["text"], keymap.get(a["level"], "norm")) for a in snap["alerts"]]

    def panel_content(self, name, snap, width) -> list[tuple[str, str]]:
        fn = {
            "header": self.header_lines, "pipeline": self.pipeline_lines,
            "workers": self.workers_lines, "funnel": self.funnel_lines,
            "ladder": self.ladder_lines, "feed": self.feed_lines,
            "alerts": self.alerts_lines,
        }[name]
        try:
            return fn(snap, width)
        except Exception as exc:            # a panel must never take the program down
            return [("panel error: %s" % exc, "err")]

    # -- overlays -----------------------------------------------------------

    def build_overlay(self, kind: str, snap) -> None:
        self.overlay = kind
        self.overlay_scroll = 0
        lines: list[tuple[str, str]] = []
        if kind == "mcp":
            lines.append(("MCP full-text handoffs awaiting the coordinator", "head"))
            lines.append(("fulltext.py cannot call an MCP tool; it parks the work here.", "dim"))
            lines.append(("", "norm"))
            for t in snap["mcp_pending"]:
                lines.append((t["evidence_id"] or "?", "accent"))
                lines.append(("  tool: %s" % t["tool"], "norm"))
                lines.append(("  args: %s" % json.dumps(t["args"], ensure_ascii=False), "norm"))
                lines.append(("  write result to: %s" % t["result_path"], "norm"))
                lines.append(("", "norm"))
            if not snap["mcp_pending"]:
                lines.append(("none pending", "dim"))
        elif kind == "missing":
            lines.append(("missing.md — quarantined full texts", "head"))
            lines.append(("", "norm"))
            text = self.reader.missing_text or "(no missing.md in this run)"
            lines += [(ln, "norm") for ln in text.splitlines()]
        elif kind == "log":
            lines.append(("engine.log (%d lines)" % len(self.reader.log_lines), "head"))
            lines.append(("", "norm"))
            lines += [(ln, "norm") for ln in self.reader.log_lines]
            self.overlay_scroll = max(0, len(lines) - 1)
        elif kind == "detail":
            lines = self.detail_lines(snap)
        self.overlay_lines = lines

    def detail_lines(self, snap) -> list[tuple[str, str]]:
        name = PANELS[self.focus]
        lines: list[tuple[str, str]] = []
        if name == "workers":
            rows = snap["workers"]
            if not rows:
                return [("no active task selected", "dim")]
            row = rows[min(self.sel[name], len(rows) - 1)]
            rec = self.reader.tasks.get(row["task_id"], {})
            lines.append(("task %s" % row["task_id"], "head"))
            for key in ("stage", "status", "worker", "attempts", "inputs_hash",
                        "output_path", "error", "created_at", "updated_at"):
                lines.append(("  %-12s %s" % (key, rec.get(key)), "norm"))
            lines.append(("  idle         %s (%s)"
                          % (fmt_dur(row["idle_seconds"]), row["stall_level"]),
                          "err" if row["stall_level"] == "red" else "norm"))
            lines.append(("", "norm"))
            lines.append(("receipt: %s" % self.reader.receipt_summary(rec), "accent"))
        elif name == "feed":
            feed = self.reader.feed
            if not feed:
                return [("no activity yet", "dim")]
            e = feed[min(self.sel[name], len(feed) - 1)]
            if e["kind"] == "log":
                lines.append(("engine.log line", "head"))
                lines.append((e["text"] or "", "norm"))
            else:
                rec = e["rec"]
                lines.append(("task %s" % e["task_id"], "head"))
                for key, val in sorted(rec.items()):
                    lines.append(("  %-14s %s" % (key, val), "norm"))
                lines.append(("", "norm"))
                lines.append(("receipt: %s" % self.reader.receipt_summary(rec), "accent"))
        elif name in ("funnel", "ladder"):
            lines.append(("corpus records (evidence_id / tier / route / last rung)", "head"))
            for r in self.reader.corpus[:400]:
                ft = r.get("fulltext") or {}
                rung = self.reader.rungs.get(r.get("evidence_id")) or {}
                tier = ft.get("source_tier")
                rung_txt = ("t%s %s" % (rung.get("tier"), rung.get("status"))
                            if rung.get("tier") is not None else "no rung recorded")
                lines.append(("%-22s tier=%-4s route=%-14s %s | %s"
                              % (r.get("evidence_id"),
                                 "null" if tier is None else tier,
                                 ft.get("access_route") or "null", rung_txt,
                                 clip(r.get("title") or "", 60)),
                              "err" if tier == 7 else
                              "dim" if tier is None else "norm"))
        elif name == "pipeline":
            lines.append(("tasks by stage", "head"))
            for tid in sorted(self.reader.tasks):
                rec = self.reader.tasks[tid]
                lines.append(("%-46s %-10s %s" % (tid, rec.get("status"),
                                                  rec.get("worker") or "-"),
                              {"failed": "err", "blocked": "warn",
                               "completed": "ok", "active": "accent"}.get(rec.get("status"),
                                                                          "norm")))
        elif name == "alerts":
            lines.append(("alerts", "head"))
            lines += self.alerts_lines(snap, 200)
        else:
            lines.append(("run configuration (config.json, verbatim)", "head"))
            lines += [(ln, "norm")
                      for ln in json.dumps(self.reader.config, indent=2,
                                           ensure_ascii=False).splitlines()]
        lines.append(("", "norm"))
        lines.append(("This monitor is read-only. To retry a failed task, run it yourself:",
                      "head"))
        lines.append(("  scripts/corpus.py task reopen --run-dir %s --task-id <task_id>"
                      % self.reader.run_dir, "accent"))
        return lines

    # -- drawing ------------------------------------------------------------

    def draw(self, stdscr) -> None:
        import curses
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        snap = snapshot(self.reader)

        def put(y, x, text, key="norm"):
            if y < 0 or y >= height:
                return
            text = clip(text, max(0, width - x - 1))
            if not text:
                return
            try:
                stdscr.addstr(y, x, text, self.attr(key))
            except curses.error:
                pass

        if self.overlay:
            title = {"mcp": "MCP handoffs", "missing": "missing.md",
                     "log": "engine.log", "detail": "detail"}[self.overlay]
            put(0, 0, " %s — Esc/q back, arrows scroll " % title, "head")
            body = height - 2
            maxscroll = max(0, len(self.overlay_lines) - body)
            self.overlay_scroll = min(self.overlay_scroll, maxscroll)
            for i in range(body):
                idx = self.overlay_scroll + i
                if idx >= len(self.overlay_lines):
                    break
                text, key = self.overlay_lines[idx]
                put(1 + i, 0, text, key)
            put(height - 1, 0, self.footer(width), "dim")
            stdscr.noutrefresh()
            curses.doupdate()
            return

        narrow = width < 60
        # header first, then the remaining panels; focused panel absorbs slack
        panels = list(PANELS)
        contents = {p: self.panel_content(p, snap, width) for p in panels}
        head = contents["header"]
        y = 0
        for i, (text, key) in enumerate(head[: max(1, min(3, height - 2))]):
            put(y, 0, text, key)
            y += 1
        if y < height - 1:
            put(y, 0, "-" * max(0, width - 1), "dim")
            y += 1

        body_panels = panels[1:]
        avail = max(0, height - y - 1)
        min_h = 3                                    # title + 2 content rows
        # drop lowest-priority panels when the terminal is too short
        priority = ["workers", "alerts", "pipeline", "feed", "funnel", "ladder"]
        shown = [p for p in body_panels]
        while shown and (min_h * len(shown)) > avail:
            for cand in reversed(priority):
                if cand in shown and cand != PANELS[self.focus]:
                    shown.remove(cand)
                    break
            else:
                break
        if not shown and avail >= min_h:
            shown = [PANELS[self.focus]] if PANELS[self.focus] != "header" else []
        if not shown or avail < min_h:
            put(y, 0, "terminal too small for the panels — resize, or use --once", "warn")
            put(height - 1, 0, self.footer(width), "dim")
            stdscr.noutrefresh()
            curses.doupdate()
            return
        heights = {}
        if shown:
            base = avail // len(shown)
            for p in shown:
                heights[p] = base
            leftover = avail - base * len(shown)
            focus_name = PANELS[self.focus]
            target = focus_name if focus_name in heights else shown[0]
            heights[target] += leftover

        for p in shown:
            h = heights[p]
            if h < 2 or y >= height - 1:
                continue
            focused = PANELS[self.focus] == p
            lines = contents[p]
            body = h - 1
            maxscroll = max(0, len(lines) - body)
            if p == "feed" and self.autoscroll and not focused:
                self.scroll[p] = maxscroll
            self.scroll[p] = min(max(0, self.scroll[p]), maxscroll)
            marker = ">" if focused else " "
            extra = ""
            if len(lines) > body:
                extra = " [%d-%d/%d]" % (self.scroll[p] + 1,
                                         min(len(lines), self.scroll[p] + body), len(lines))
            title = "%s%s%s" % (marker, PANEL_TITLE[p], extra)
            put(y, 0, title.ljust(max(0, width - 1)), "sel" if focused else "head")
            y += 1
            for i in range(body):
                idx = self.scroll[p] + i
                if idx >= len(lines):
                    break
                text, key = lines[idx]
                pointer = ""
                if focused and p in ("workers", "feed") and idx == self.sel[p]:
                    pointer = "*"
                    key = "sel"
                put(y, 0, ("%s%s" % (pointer.ljust(1) if focused else "", text))
                    if focused else text, key)
                y += 1
            if narrow:
                continue
        put(height - 1, 0, self.footer(width), "dim")
        stdscr.noutrefresh()
        curses.doupdate()

    def footer(self, width) -> str:
        base = ("q quit  Tab/1-7 panel  arrows/PgUp/PgDn scroll  Enter detail  "
                "m mcp  x missing  l log  p pause  r reload")
        if self.status:
            base = self.status + " | " + base
        state = " [PAUSED]" if not self.autoscroll else ""
        return clip(base + state, width - 1)

    # -- input --------------------------------------------------------------

    def handle_key(self, key: int, snap) -> bool:
        """Returns False to quit."""
        import curses
        if key in (ord("q"), ord("Q")):
            if self.overlay:
                self.overlay = None
                return True
            return False
        if key == 27:                          # Esc
            self.overlay = None
            return True
        if self.overlay:
            page = 10
            if key in (curses.KEY_DOWN, ord("j")):
                self.overlay_scroll += 1
            elif key in (curses.KEY_UP, ord("k")):
                self.overlay_scroll = max(0, self.overlay_scroll - 1)
            elif key == curses.KEY_NPAGE:
                self.overlay_scroll += page
            elif key == curses.KEY_PPAGE:
                self.overlay_scroll = max(0, self.overlay_scroll - page)
            elif key == curses.KEY_HOME:
                self.overlay_scroll = 0
            return True
        name = PANELS[self.focus]
        if key == ord("\t") or key == 9:
            self.focus = (self.focus + 1) % len(PANELS)
        elif key == curses.KEY_BTAB:
            self.focus = (self.focus - 1) % len(PANELS)
        elif ord("1") <= key <= ord("7"):
            self.focus = key - ord("1")
        elif key in (curses.KEY_DOWN, ord("j")):
            self.scroll[name] += 1
            self.sel[name] += 1
        elif key in (curses.KEY_UP, ord("k")):
            self.scroll[name] = max(0, self.scroll[name] - 1)
            self.sel[name] = max(0, self.sel[name] - 1)
        elif key == curses.KEY_NPAGE:
            self.scroll[name] += 10
            self.sel[name] += 10
        elif key == curses.KEY_PPAGE:
            self.scroll[name] = max(0, self.scroll[name] - 10)
            self.sel[name] = max(0, self.sel[name] - 10)
        elif key == curses.KEY_HOME:
            self.scroll[name] = 0
            self.sel[name] = 0
        elif key in (curses.KEY_ENTER, 10, 13):
            self.build_overlay("detail", snap)
        elif key in (ord("m"), ord("M")):
            self.build_overlay("mcp", snap)
        elif key in (ord("x"), ord("X")):
            self.build_overlay("missing", snap)
        elif key in (ord("l"), ord("L")):
            self.build_overlay("log", snap)
        elif key in (ord("p"), ord("P")):
            self.autoscroll = not self.autoscroll
            self.status = "autoscroll %s" % ("on" if self.autoscroll else "paused")
        elif key in (ord("r"), ord("R")):
            self.reader.poll(force=True)
            self.status = "full reload"
        return True

    # -- loop ---------------------------------------------------------------

    def run(self, stdscr) -> None:
        import curses
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.keypad(True)
        self.init_colors()
        self.reader.poll(force=True)
        last_poll = 0.0
        self.draw(stdscr)
        while True:
            now = time.time()
            if now - last_poll >= self.interval:
                self.reader.poll()
                last_poll = now
                self.draw(stdscr)
            try:
                key = stdscr.getch()
            except curses.error:
                key = -1
            if key == curses.KEY_RESIZE or self.resized:
                self.resized = False
                try:
                    curses.update_lines_cols()
                except (AttributeError, curses.error):
                    pass
                stdscr.erase()
                self.draw(stdscr)
                continue
            if key == -1:
                time.sleep(0.05)
                continue
            snap = snapshot(self.reader)
            if not self.handle_key(key, snap):
                return
            self.draw(stdscr)


# --------------------------------------------------------------------------- cli


def latest_run(wiki_root: Path) -> Path:
    base = wiki_root / "outputs" / "deep-research"
    if not base.is_dir():
        raise SystemExit("no run directory under %s" % base)
    runs = [d for d in base.iterdir() if d.is_dir()]
    if not runs:
        raise SystemExit("no runs under %s" % base)
    return max(runs, key=lambda d: d.stat().st_mtime)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="watch.py",
        description="Read-only run monitor for a deep-research run directory. "
                    "Never writes to the run, never takes the taskboard lock, "
                    "never uses the network.",
        epilog="Keys: q quit | Tab or 1-7 focus a panel | arrows/PgUp/PgDn scroll | "
               "Enter detail | m MCP handoffs | x missing.md | l engine.log | "
               "p pause autoscroll | r force full reload",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--run-dir", help="the run directory to attach to")
    src.add_argument("--follow-latest", metavar="WIKI",
                     help="attach to the newest run under <WIKI>/outputs/deep-research/")
    p.add_argument("--once", action="store_true",
                   help="print one plain-text snapshot and exit (no curses, no TTY needed)")
    p.add_argument("--json", action="store_true",
                   help="print one machine-readable JSON snapshot and exit")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                   help="poll interval in seconds (default %(default)s)")
    p.add_argument("--stall-seconds", type=float, default=DEFAULT_STALL,
                   help="an active task idle this long is amber, 2x is red "
                        "(default %(default)s)")
    p.add_argument("--width", type=int, default=100,
                   help="line width for --once (default %(default)s)")
    p.add_argument("--no-color", action="store_true",
                   help="disable colour (NO_COLOR in the environment does the same)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.run_dir:
        run_dir = Path(args.run_dir).expanduser()
    else:
        run_dir = latest_run(Path(args.follow_latest).expanduser())
    if not run_dir.is_dir():
        sys.stderr.write("watch.py: not a directory: %s\n" % run_dir)
        return 2
    reader = RunReader(run_dir, stall_seconds=args.stall_seconds)
    reader.poll(force=True)

    if args.json:
        json.dump(snapshot(reader), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    if args.once:
        sys.stdout.write(render_text(reader, args.width) + "\n")
        return 0

    use_color = not args.no_color and not os.environ.get("NO_COLOR")
    try:
        import curses
    except ImportError:
        sys.stderr.write("watch.py: curses unavailable; falling back to --once\n")
        sys.stdout.write(render_text(reader, args.width) + "\n")
        return 0
    if not sys.stdout.isatty():
        sys.stderr.write("watch.py: stdout is not a TTY; falling back to --once\n")
        sys.stdout.write(render_text(reader, args.width) + "\n")
        return 0

    tui = Tui(reader, args.interval, use_color)

    def on_winch(_sig, _frm):
        tui.resized = True

    try:
        signal.signal(signal.SIGWINCH, on_winch)
    except (AttributeError, ValueError):
        pass
    try:
        curses.wrapper(tui.run)             # restores the terminal on any exit path
    except KeyboardInterrupt:
        pass
    except curses.error as exc:
        sys.stderr.write("watch.py: curses failed (%s); falling back to --once\n" % exc)
        sys.stdout.write(render_text(reader, args.width) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
