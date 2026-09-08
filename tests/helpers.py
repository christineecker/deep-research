from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load_script(name: str):
    path = SCRIPTS / name
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    try:
        assert spec.loader is not None
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(str(SCRIPTS))
        except ValueError:
            pass
    return module


def make_run(tmp_path: Path, slug: str = "validation-run") -> tuple[Path, Path]:
    wiki = tmp_path / "wiki"
    run = wiki / "outputs" / "deep-research" / slug
    for rel in ("outputs", "workspace/extractions", "workspace/appraisals", "sources"):
        (run / rel).mkdir(parents=True, exist_ok=True)
    (wiki / "assets" / "papers").mkdir(parents=True, exist_ok=True)
    write_json(run / "config.json", {
        "schema_version": 1,
        "created_at": "2026-09-08T00:00:00Z",
        "question": "Validation architecture smoke test",
        "gates": {"evidence_kernel": False},
    })
    return wiki, run


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                    encoding="utf-8")


def run_py(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=cwd or ROOT, text=True,
                          capture_output=True, timeout=120)


def minimal_corpus_record(evidence_id: str = "pmid:12345678") -> dict:
    return {
        "evidence_id": evidence_id,
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
        "source_ids": [],
    }
