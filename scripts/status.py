#!/usr/bin/env python3
"""status.py — run status overview with stage progress and record access table.

Shows:
  - current stage with progress (e.g., Stage 3: Retrieve - 12/25 completed)
  - formatted table: PMID | Title | Authors | PDF Status (fulltext/abstract/missing)
  - filtering options (e.g., --missing to show only records without PDFs)

Environment: python3 3.14, stdlib only. No third-party imports, no pip.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import now_iso, read_json  # noqa: E402

# Stage names in order
STAGES = (
    "protocol", "search", "screen", "adjudicate", "retrieve", "extract",
    "appraise", "synthesize", "verify", "report", "okf",
)

STAGE_LABELS = {
    "protocol": "Stage 0: Protocol & Setup",
    "search": "Stage 1: Literature Search",
    "screen": "Stage 2: Screening",
    "adjudicate": "Stage 2b: Adjudication",
    "retrieve": "Stage 3: Retrieve Full Text",
    "extract": "Stage 4: Extract Data",
    "appraise": "Stage 5: Critical Appraisal",
    "synthesize": "Stage 6: Synthesis",
    "verify": "Stage 7: Verification",
    "report": "Stage 8: Report Generation",
    "okf": "Stage 9: OKF Promotion",
}

FULLTEXT_ICONS = {
    "fulltext": "✓ Fulltext",
    "abstract_only": "~ Abstract",
    "missing": "✗ Missing",
}


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSON Lines file. Return empty list if file doesn't exist."""
    try:
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        return [json.loads(line) for line in lines if line.strip()]
    except (FileNotFoundError, OSError):
        return []


def get_current_stage(run_dir: Path) -> tuple[str | None, dict]:
    """Determine current stage by analyzing taskboard.

    Returns (current_stage_name, stage_progress_dict).
    stage_progress_dict maps stage -> {pending, active, completed, blocked, failed}.
    """
    taskboard = read_jsonl(run_dir / "taskboard.jsonl")
    if not taskboard:
        return None, {}

    # Build stage progress map
    stage_progress = {stage: {"pending": 0, "active": 0, "completed": 0, "blocked": 0, "failed": 0}
                      for stage in STAGES}

    # Track which stages are done
    task_by_id = {}
    for rec in taskboard:
        task_by_id[rec.get("task_id")] = rec

    for rec in task_by_id.values():
        stage = rec.get("stage")
        status = rec.get("status")
        if stage in stage_progress and status in stage_progress[stage]:
            stage_progress[stage][status] += 1

    # Find the current (furthest) stage that has activity
    current = None
    for stage in STAGES:
        progress = stage_progress[stage]
        total = sum(progress.values())
        if total > 0:
            current = stage

    return current, stage_progress


def progress_bar(completed: int, total: int, width: int = 40) -> str:
    """Generate a progress bar."""
    if total <= 0:
        return "[" + " " * width + "]"
    fraction = completed / total
    filled = int(fraction * width)
    empty = width - filled
    return "[" + "█" * filled + "░" * empty + f"] {completed}/{total}"


def format_stage_status(stage: str | None, progress: dict) -> str:
    """Format stage progress line."""
    if not stage:
        return "No stages started yet."

    label = STAGE_LABELS.get(stage, stage)
    p = progress.get(stage, {})

    completed = p.get("completed", 0)
    total = sum(p.values())
    active = p.get("active", 0)
    pending = p.get("pending", 0)
    failed = p.get("failed", 0)
    blocked = p.get("blocked", 0)

    if total == 0:
        status_part = "(idle)"
    elif active > 0:
        status_part = f"(active: {active}/{total}, {completed} done)"
    elif failed > 0:
        status_part = f"({failed} failed)"
    elif blocked > 0:
        status_part = f"({blocked} blocked)"
    elif completed == total:
        status_part = "(complete)"
    else:
        status_part = f"({pending} pending, {completed} done)"

    bar = progress_bar(completed, total)
    return f"{label}\n  {bar}  {status_part}"


def format_corpus_table(run_dir: Path, filter_missing: bool = False, limit: int = 100) -> str:
    """Format corpus records as a table."""
    corpus = read_jsonl(run_dir / "corpus.jsonl")
    if not corpus:
        return "No records yet."

    # Filter if requested
    if filter_missing:
        corpus = [r for r in corpus if (r.get("fulltext") or {}).get("status") == "missing"]

    # Limit output
    corpus = corpus[:limit]

    # Build table rows
    rows = []
    for rec in corpus:
        pmid = rec.get("pmid") or "?"
        title = (rec.get("title") or "Untitled")[:70]

        # Authors (last name, first initial)
        authors_raw = rec.get("authors") or []
        author_str = ""
        if authors_raw:
            if isinstance(authors_raw, list):
                names = []
                for auth in authors_raw[:3]:  # first 3 authors
                    name = None
                    if isinstance(auth, dict):
                        # Handle NCBI-style dict with 'family' and 'given' keys
                        last = auth.get("family") or auth.get("last_name") or ""
                        first = auth.get("given") or auth.get("first_names") or ""
                        if first:
                            first = first.split()[0][0].upper() + "."  # first initial
                        if last:
                            name = f"{last} {first}".strip()
                    elif isinstance(auth, str):
                        # Try to parse as dict repr string first
                        try:
                            d = ast.literal_eval(auth)  # safe parse of dict-like string
                            if isinstance(d, dict):
                                last = d.get("family") or d.get("last_name") or ""
                                first = d.get("given") or d.get("first_names") or ""
                                if first:
                                    first = first.split()[0][0].upper() + "."
                                if last:
                                    name = f"{last} {first}".strip()
                        except (ValueError, SyntaxError):
                            # Fall back to simple parsing
                            parts = auth.split(",")
                            if parts:
                                name = parts[0].strip()
                    if name:
                        names.append(name)
                if len(authors_raw) > 3:
                    names.append("et al")
                author_str = ", ".join(names)
        author_str = author_str[:50] if author_str else "?"

        # Fulltext status
        ft = rec.get("fulltext") or {}
        status = ft.get("status") or "unknown"
        icon = FULLTEXT_ICONS.get(status, "? " + status)

        # Screening decision (if available)
        screening = rec.get("screening") or {}
        screen_decision = screening.get("decision") or ""

        rows.append({
            "pmid": pmid,
            "title": title,
            "authors": author_str,
            "status": icon,
            "screen": screen_decision,
            "extracted": "✓" if rec.get("extraction_path") else "",
            "appraised": "✓" if rec.get("appraisal_path") else "",
        })

    if not rows:
        return "No records match filter."

    # Calculate column widths
    pmid_w = max(8, max(len(r["pmid"]) for r in rows))
    title_w = max(30, min(70, max(len(r["title"]) for r in rows)))
    authors_w = max(15, min(50, max(len(r["authors"]) for r in rows)))
    status_w = 12
    screen_w = 8
    extract_w = 3
    appraise_w = 3

    # Build table
    lines = []
    header = (
        f"{'PMID':<{pmid_w}}  "
        f"{'Title':<{title_w}}  "
        f"{'Authors':<{authors_w}}  "
        f"{'PDF Status':<{status_w}}  "
        f"{'Screen':<{screen_w}}  "
        f"{'Ext':<{extract_w}}  "
        f"{'Apr':<{appraise_w}}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    for row in rows:
        line = (
            f"{row['pmid']:<{pmid_w}}  "
            f"{row['title']:<{title_w}}  "
            f"{row['authors']:<{authors_w}}  "
            f"{row['status']:<{status_w}}  "
            f"{row['screen']:<{screen_w}}  "
            f"{row['extracted']:<{extract_w}}  "
            f"{row['appraised']:<{appraise_w}}"
        )
        lines.append(line)

    return "\n".join(lines)


def format_summary(run_dir: Path) -> str:
    """Format a complete status summary."""
    config = read_json(run_dir / "config.json") or {}
    current_stage, stage_progress = get_current_stage(run_dir)
    corpus = read_jsonl(run_dir / "corpus.jsonl")

    lines = []

    # Header
    slug = config.get("slug") or run_dir.name
    question = config.get("question") or "(no question recorded)"
    lines.append(f"\n{'='*80}")
    lines.append(f"  Run: {slug}")
    lines.append(f"  Profile: {config.get('profile', '?')} | Scope: {config.get('scope', '?')} | Rigor: {config.get('rigor', '?')}")
    lines.append(f"{'='*80}\n")

    if question:
        lines.append(f"Question: {question}\n")

    # Current stage
    lines.append("CURRENT STAGE")
    lines.append("-" * 80)
    lines.append(format_stage_status(current_stage, stage_progress))
    lines.append("")

    # Corpus summary
    if corpus:
        screened = sum(1 for r in corpus if r.get("screening"))
        included = sum(1 for r in corpus if (r.get("screening") or {}).get("decision") == "include")
        excluded = sum(1 for r in corpus if (r.get("screening") or {}).get("decision") == "exclude")
        unclear = sum(1 for r in corpus if (r.get("screening") or {}).get("decision") == "unclear")

        fulltext = sum(1 for r in corpus if (r.get("fulltext") or {}).get("status") == "fulltext")
        abstract_only = sum(1 for r in corpus if (r.get("fulltext") or {}).get("status") == "abstract_only")
        missing = sum(1 for r in corpus if (r.get("fulltext") or {}).get("status") == "missing")

        extracted = sum(1 for r in corpus if r.get("extraction_path"))
        appraised = sum(1 for r in corpus if r.get("appraisal_path"))

        lines.append("CORPUS SUMMARY")
        lines.append("-" * 80)
        lines.append(f"  Total records: {len(corpus)}")
        lines.append(f"  Screened: {screened} (included: {included}, excluded: {excluded}, unclear: {unclear})")
        lines.append(f"  Full text: {fulltext} | Abstract only: {abstract_only} | Missing: {missing}")
        lines.append("")

        # Extraction/appraisal progress
        lines.append("EXTRACTION & APPRAISAL")
        lines.append("-" * 80)
        extract_bar = progress_bar(extracted, included)
        appraise_bar = progress_bar(appraised, included)
        lines.append(f"  Extracted:  {extract_bar}")
        lines.append(f"  Appraised:  {appraise_bar}")

        # Completion message
        if included > 0 and extracted == included and appraised == included:
            lines.append("")
            lines.append("  ✓ ALL EXTRACTION AND APPRAISAL COMPLETE")

        lines.append("")

    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="status.py",
        description="Show run status: current stage and record access overview.",
    )
    parser.add_argument("run_dir", help="path to the run directory")
    parser.add_argument("--table", action="store_true", help="show full corpus table")
    parser.add_argument("--missing", action="store_true", help="show only records without full text")
    parser.add_argument("--limit", type=int, default=100, help="max rows in table (default 100)")
    parser.add_argument("--no-summary", action="store_true", help="skip summary, show only table")

    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir).expanduser()

    if not run_dir.is_dir():
        sys.stderr.write(f"status.py: not a directory: {run_dir}\n")
        return 2

    # Show summary unless --no-summary
    if not args.no_summary:
        print(format_summary(run_dir))

    # Show table if requested
    if args.table or args.missing:
        print("\nRECORDS")
        print("-" * 80)
        print(format_corpus_table(run_dir, filter_missing=args.missing, limit=args.limit))

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
