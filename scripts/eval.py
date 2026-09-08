#!/usr/bin/env python3
"""Deterministic validation-architecture evals for the deep-research evidence kernel.

The harness builds synthetic run directories, runs `assemble.py`, and checks that the
accepted/rejected result matches `eval/cases.json`. It is deliberately local and stdlib-only:
no network, no PubMed calls, no real wiki mutation.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import assemble  # noqa: E402
import store  # noqa: E402


TEXT = "Methods. The trial enrolled 42 adults and followed them for 12 weeks."
EVIDENCE_ID = "pmid:12345678"
PAPER = {"pmid": "12345678", "doi": "10.1000/validation", "pmcid": "PMC1234567"}


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                    encoding="utf-8")


def make_run(base: Path, slug: str) -> tuple[Path, Path]:
    wiki = base / "wiki"
    run = wiki / "outputs" / "deep-research" / slug
    for rel in ("outputs", "workspace/extractions", "workspace/appraisals", "sources"):
        (run / rel).mkdir(parents=True, exist_ok=True)
    write_json(run / "config.json", {
        "schema_version": 1,
        "created_at": "2026-09-08T00:00:00Z",
        "question": "Evidence-kernel eval",
        "gates": {"evidence_kernel": False},
    })
    write_jsonl(run / "corpus.jsonl", [{
        "evidence_id": EVIDENCE_ID,
        "status": "included",
        "title": "A compact validation trial",
        "pmid": "12345678",
        "doi": "10.1000/validation",
        "pmcid": "PMC1234567",
        "authors": [{"family": "Smith", "given": "Jane A", "initials": "JA"}],
        "journal": "Journal of Validation",
        "publication_date": "2026-01-01",
        "abstract": "A compact validation trial enrolled 42 adults.",
        "basis": "full_text",
    }])
    return wiki, run


def source_for_mode(run: Path, mode: str) -> tuple[str, int, int, str]:
    paper = None if mode == "no_paper_id" else PAPER
    if mode == "registered":
        snap = store.register_text(
            run, url="https://example.org/fulltext", text=TEXT, title="Full text",
            access="full_text", origin="pubmed", paper=paper, actor="eval")
    else:
        snap = store.write_snapshot(
            run, url="https://example.org/fulltext", text=TEXT, title="Full text",
            access="full_text", origin="pubmed", paper=paper,
            event_type="fetch", fresh=True, actor="eval")
    start = TEXT.index("trial enrolled")
    end = TEXT.index(" and followed")
    if mode == "tamper":
        path = run / "sources" / f"{snap['source_id']}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["text"] += "\nTampered."
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return snap["source_id"], start, end, TEXT[start:end]


def write_extraction(run: Path, mode: str) -> None:
    if mode == "legacy":
        write_json(run / "workspace" / "extractions" / "case.json", {
            "evidence_id": EVIDENCE_ID,
            "design": "trial",
            "quotes": [{"text": "The trial enrolled 42 adults."}],
        })
        return

    if mode == "unknown_source":
        source_id, start, end, excerpt = "src-" + ("0" * 64), 9, 34, "trial enrolled 42 adults"
    else:
        source_id, start, end, excerpt = source_for_mode(run, mode)
    quote_text = "trial enrolled 41 adults" if mode == "mismatch" else excerpt
    write_json(run / "workspace" / "extractions" / "case.json", {
        "evidence_id": EVIDENCE_ID,
        "spans": [{
            "claim": "The trial enrolled 42 adults",
            "source_id": source_id,
            "start": start,
            "end": end,
            "text": excerpt,
        }],
        "quotes": [{
            "source_id": source_id,
            "start": start,
            "end": end,
            "text": quote_text,
        }],
    })


def write_report(run: Path) -> None:
    (run / "outputs" / "report.md").write_text(
        "# Evidence-kernel eval report\n\n"
        "The trial enrolled 42 adults.[^pubmed-12345678]\n\n"
        "## References\n\n"
        "[^pubmed-12345678]: Smith JA. Journal of Validation. PMID 12345678. "
        "DOI 10.1000/validation. PubMed "
        "https://pubmed.ncbi.nlm.nih.gov/12345678/\n",
        encoding="utf-8")


def run_cli(args: list[str]) -> dict:
    proc = subprocess.run([sys.executable, *args], cwd=ROOT, text=True,
                          capture_output=True, timeout=120)
    return {"returncode": proc.returncode, "stdout": proc.stdout,
            "stderr": proc.stderr}


def run_case(case: dict, out_root: Path) -> dict:
    slug = case["id"]
    base = out_root / slug
    if base.exists():
        shutil.rmtree(base)
    _wiki, run = make_run(base, slug)
    write_extraction(run, case["mode"])
    write_report(run)

    result = assemble.Assembler(run).run()
    assemble.write_result(result, run / "outputs" / "result.json")
    verify_run = run_cli([
        "scripts/verify.py", "run", "--run-dir", str(run), "--report",
        str(run / "outputs" / "report.md"), "--json",
    ])
    verify_kernel = {}
    try:
        verify_payload = json.loads(verify_run["stdout"])
        verify_kernel = {c.get("check_id"): c.get("status")
                         for c in verify_payload.get("checks") or []
                         if c.get("check_id") in ("C-SNAPSHOT", "C-SPAN",
                                                  "C-FRESH-FETCH", "C-ASSEMBLER")}
    except json.JSONDecodeError:
        verify_kernel = {"_parse_error": "verify.py emitted non-JSON"}

    init_run = run_cli(["scripts/okf.py", "init", "--wiki", str(_wiki)])
    promote_args = [
        "scripts/okf.py", "promote", "--run-dir", str(run), "--wiki", str(_wiki),
        "--allow-unverified", "--force", "--check",
    ]
    promote_run = run_cli(promote_args)
    reasons = result.get("diagnostics", {}).get("counts_by_reason", {})
    expected_reason = case.get("expect_reason")
    expected_verdict = case.get("expect_verdict")
    ok = result.get("gate", {}).get("verdict") == expected_verdict
    if expected_reason is None:
        ok = ok and not reasons and result.get("counts", {}).get("accepted", 0) >= 1
    else:
        ok = ok and reasons.get(expected_reason) == 1
    ok = ok and verify_run["returncode"] in (0, 1) and init_run["returncode"] == 0
    if case["mode"] == "valid":
        ok = ok and promote_run["returncode"] == 0
    if case["mode"] in ("tamper", "unknown_source", "no_paper_id", "mismatch"):
        ok = ok and promote_run["returncode"] != 0
    return {
        "id": case["id"],
        "ok": ok,
        "mode": case["mode"],
        "expected_verdict": expected_verdict,
        "actual_verdict": result.get("gate", {}).get("verdict"),
        "expected_reason": expected_reason,
        "reasons": reasons,
        "verify_kernel": verify_kernel,
        "verify_returncode": verify_run["returncode"],
        "okf_promote_check_returncode": promote_run["returncode"],
        "accepted": result.get("counts", {}).get("accepted"),
        "unresolved": result.get("counts", {}).get("unresolved"),
        "run_dir": str(run),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(ROOT / "eval" / "cases.json"))
    parser.add_argument("--out", help="directory for synthetic eval runs")
    args = parser.parse_args(argv)

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise SystemExit("--cases must be a JSON list")
    if args.out:
        out_root = Path(args.out).expanduser().resolve()
        out_root.mkdir(parents=True, exist_ok=True)
        cleanup = None
    else:
        cleanup = tempfile.TemporaryDirectory(prefix="deep-research-eval-")
        out_root = Path(cleanup.name)

    results = [run_case(case, out_root) for case in cases]
    summary = {
        "ok": all(r["ok"] for r in results),
        "passed": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "cases": results,
        "out": str(out_root),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if cleanup is not None:
        cleanup.cleanup()
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
