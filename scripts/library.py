#!/usr/bin/env python3
"""library.py — shared cross-run PDF library at <wiki-root>/assets/papers/.

Rung 0 of the full-text acquisition ladder (PLAN.md §5) plus the inbox resume loop.

Subcommands
-----------
  init          create assets/papers/ + index.json, rewrite the wiki .gitignore
  lookup        match a record by DOI / PMID / fuzzy title
  add           file a PDF into the library (sha256 content dedupe)
  ingest-inbox  match every PDF in <run-dir>/inbox/ to a quarantined corpus record
  list          dump index entries

Everything here is stdlib only (pdftotext / pdfinfo / pdftoppm / tesseract binaries
are shelled out to). No pip installs. See references/acquisition.md.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

SCHEMA_VERSION = 1

# --- matching thresholds (documented in references/acquisition.md) ---------------
# title -> title comparison (index lookup); difflib.SequenceMatcher.ratio()
FUZZY_TITLE_THRESHOLD = 0.90
# title -> first-page-text sliding window comparison (inbox ingestion); looser,
# because OCR/layout noise inflates the denominator.
FUZZY_PAGE_THRESHOLD = 0.85
# a title shorter than this is never fuzzy-matched (too collision-prone)
MIN_FUZZY_TITLE_CHARS = 25

DOI_RE = re.compile(r"10\.\d{4,}/\S+")
MIN_TEXT_CHARS = 100  # below this, pdftotext is considered to have failed

GITIGNORE_RULES = [
    "assets/*",
    "!assets/papers/",
    "assets/papers/*",
    "!assets/papers/index.json",
]
GITIGNORE_BEGIN = "# >>> deep-research pdf library >>>"
GITIGNORE_END = "# <<< deep-research pdf library <<<"
# lines that would shadow the managed block and are therefore replaced by it
GITIGNORE_SUPERSEDED = {"assets", "assets/", "assets/*", "/assets", "/assets/", "/assets/*"} | set(
    GITIGNORE_RULES
)


# ---------------------------------------------------------------- helpers ------


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:", "https://dx.doi.org/"):
        if d.startswith(prefix):
            d = d[len(prefix) :]
    d = d.rstrip(").,;]>'\"")
    return d or None


def normalize_title(title: str | None) -> str:
    """Case-fold, strip accents/punctuation, collapse whitespace."""
    if not title:
        return ""
    t = unicodedata.normalize("NFKD", title)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.casefold()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def title_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def title_in_text_ratio(title_norm: str, text_norm: str) -> float:
    """Best sliding-window similarity of a normalized title inside a page of text."""
    if not title_norm or not text_norm:
        return 0.0
    if title_norm in text_norm:
        return 1.0
    n = len(title_norm)
    if len(text_norm) <= n:
        return title_ratio(title_norm, text_norm)
    step = max(1, n // 4)
    best = 0.0
    for start in range(0, len(text_norm) - n + 1, step):
        window = text_norm[start : start + n + step]
        r = title_ratio(title_norm, window)
        if r > best:
            best = r
            if best >= 0.995:
                break
    return best


def slugify(value: str, maxlen: int = 80) -> str:
    s = re.sub(r"[^A-Za-z0-9._~-]+", "-", (value or "").strip())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return (s[:maxlen] or "unknown")


def evidence_id_of(rec: dict) -> str:
    """Rule S9 of references/schema.md."""
    if rec.get("evidence_id"):
        return rec["evidence_id"]
    if rec.get("pmid"):
        return "pmid:%s" % rec["pmid"]
    if rec.get("doi"):
        return "doi:%s" % normalize_doi(rec["doi"])
    if rec.get("pmcid"):
        return "pmcid:%s" % rec["pmcid"]
    url = rec.get("url") or rec.get("title") or ""
    return "url:%s" % sha256_text(url)[:16]


def record_stem(rec: dict) -> str:
    """Filename stem used in the library and workspace for a corpus record."""
    if rec.get("pmid"):
        return "pmid-%s" % rec["pmid"]
    if rec.get("doi"):
        return "doi-%s" % slugify(normalize_doi(rec["doi"]).replace("/", "-"))
    if rec.get("pmcid"):
        return "pmcid-%s" % rec["pmcid"]
    return slugify(evidence_id_of(rec).replace(":", "-"))


# ------------------------------------------------------------- pdf plumbing ----


def _run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def pdf_pages(pdf: Path) -> int | None:
    if not shutil.which("pdfinfo"):
        return None
    try:
        proc = _run(["pdfinfo", str(pdf)], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def pdftotext(pdf: Path, first: int | None = None, last: int | None = None) -> str:
    """`pdftotext -layout`, optionally page-limited. Falls back to pdfminer."""
    if shutil.which("pdftotext"):
        cmd = ["pdftotext", "-layout"]
        if first is not None:
            cmd += ["-f", str(first)]
        if last is not None:
            cmd += ["-l", str(last)]
        cmd += [str(pdf), "-"]
        try:
            proc = _run(cmd)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    try:  # pdfminer is the only third-party fallback that is installed
        from pdfminer.high_level import extract_text  # type: ignore

        kwargs = {}
        if first is not None and last is not None:
            kwargs["page_numbers"] = list(range(first - 1, last))
        return extract_text(str(pdf), **kwargs) or ""
    except Exception:
        return ""


def ocr_pdf(pdf: Path, max_pages: int = 30) -> str:
    """Per-page OCR fallback: pdftoppm -> tesseract. ocrmypdf is NOT installed."""
    if not (shutil.which("pdftoppm") and shutil.which("tesseract")):
        return ""
    pages = pdf_pages(pdf) or 1
    out: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        for page in range(1, min(pages, max_pages) + 1):
            stem = os.path.join(td, "p%03d" % page)
            try:
                rc = _run(
                    ["pdftoppm", "-r", "300", "-gray", "-png", "-f", str(page), "-l", str(page),
                     str(pdf), stem],
                    timeout=300,
                )
                if rc.returncode != 0:
                    continue
                images = sorted(Path(td).glob("p%03d*.png" % page))
                for img in images:
                    t = _run(["tesseract", str(img), "-", "--psm", "1"], timeout=300)
                    if t.returncode == 0:
                        out.append(t.stdout)
                    img.unlink(missing_ok=True)
            except (OSError, subprocess.SubprocessError):
                continue
    return "\n".join(out)


def pdf_text_with_ocr(pdf: Path) -> tuple[str, bool]:
    """Return (text, used_ocr). OCR only when pdftotext yields < MIN_TEXT_CHARS."""
    text = pdftotext(pdf)
    if len(text.strip()) >= MIN_TEXT_CHARS:
        return text, False
    ocr = ocr_pdf(pdf)
    if len(ocr.strip()) > len(text.strip()):
        return ocr, True
    return text, False


def doi_from_pdf(pdf: Path) -> str | None:
    """DOI regex over page-1 text (pdfinfo metadata rarely carries it; PLAN.md §5)."""
    text = pdftotext(pdf, first=1, last=1)
    if len(text.strip()) < MIN_TEXT_CHARS:
        text = (text or "") + "\n" + ocr_pdf(pdf, max_pages=1)
    m = DOI_RE.search(text or "")
    return normalize_doi(m.group(0)) if m else None


# ------------------------------------------------------------------ library ----


class Library:
    """The <wiki-root>/assets/papers/ store and its index.json manifest."""

    def __init__(self, wiki_root: Path):
        self.wiki_root = Path(wiki_root).expanduser().resolve()
        self.papers_dir = self.wiki_root / "assets" / "papers"
        self.index_path = self.papers_dir / "index.json"
        self.index = self._load()

    # -- persistence --
    def _load(self) -> dict:
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("entries"), list):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"schema_version": SCHEMA_VERSION, "updated_at": utcnow(), "entries": []}

    def save(self) -> None:
        self.papers_dir.mkdir(parents=True, exist_ok=True)
        self.index["schema_version"] = SCHEMA_VERSION
        self.index["updated_at"] = utcnow()
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self.index_path)

    @property
    def entries(self) -> list[dict]:
        return self.index["entries"]

    # -- init --
    def init(self) -> dict:
        self.papers_dir.mkdir(parents=True, exist_ok=True)
        created_index = not self.index_path.exists()
        self.save()
        gitignore = rewrite_gitignore(self.wiki_root / ".gitignore")
        return {
            "wiki_root": str(self.wiki_root),
            "papers_dir": str(self.papers_dir),
            "index_created": created_index,
            "gitignore": gitignore,
        }

    # -- matching --
    def lookup(
        self,
        doi: str | None = None,
        pmid: str | None = None,
        pmcid: str | None = None,
        title: str | None = None,
        sha256: str | None = None,
    ) -> tuple[dict | None, str | None, float]:
        """Return (entry, match_kind, score). Order: sha256, DOI, PMID, PMCID, fuzzy title."""
        if sha256:
            for e in self.entries:
                if e.get("sha256") == sha256:
                    return e, "sha256", 1.0
        ndoi = normalize_doi(doi)
        if ndoi:
            for e in self.entries:
                if normalize_doi(e.get("doi")) == ndoi:
                    return e, "doi", 1.0
        if pmid:
            for e in self.entries:
                if str(e.get("pmid") or "") == str(pmid):
                    return e, "pmid", 1.0
        if pmcid:
            for e in self.entries:
                if (e.get("pmcid") or "").upper() == pmcid.upper():
                    return e, "pmcid", 1.0
        tnorm = normalize_title(title)
        if tnorm and len(tnorm) >= MIN_FUZZY_TITLE_CHARS:
            best, best_score = None, 0.0
            for e in self.entries:
                score = title_ratio(tnorm, e.get("title_norm") or normalize_title(e.get("title")))
                if score > best_score:
                    best, best_score = e, score
            if best is not None and best_score >= FUZZY_TITLE_THRESHOLD:
                return best, "fuzzy_title", best_score
        return None, None, 0.0

    def entry_path(self, entry: dict) -> Path:
        return self.wiki_root / entry["path"]

    # -- add --
    def add(
        self,
        pdf: Path,
        *,
        pmid: str | None = None,
        doi: str | None = None,
        pmcid: str | None = None,
        title: str | None = None,
        journal: str | None = None,
        year: str | None = None,
        source_tier: int | None = None,
        access_route: str | None = None,
        stem: str | None = None,
        move: bool = False,
    ) -> tuple[dict, bool]:
        """File a PDF into the library. Returns (entry, was_new). sha256 content dedupe."""
        pdf = Path(pdf)
        digest = sha256_file(pdf)
        existing, _, _ = self.lookup(sha256=digest)
        if existing is not None:
            # dedupe: keep the stored copy, enrich missing identifiers
            for key, val in (
                ("pmid", pmid), ("doi", normalize_doi(doi)), ("pmcid", pmcid),
                ("title", title), ("journal", journal), ("year", year),
            ):
                if val and not existing.get(key):
                    existing[key] = val
            if title and not existing.get("title_norm"):
                existing["title_norm"] = normalize_title(title)
            self.save()
            if move:
                pdf.unlink(missing_ok=True)
            return existing, False

        self.papers_dir.mkdir(parents=True, exist_ok=True)
        base = stem or (
            "pmid-%s" % pmid if pmid
            else "doi-%s" % slugify(normalize_doi(doi).replace("/", "-")) if doi
            else "pmcid-%s" % pmcid if pmcid
            else "sha-%s" % digest[:16]
        )
        dest = self.papers_dir / ("%s.pdf" % base)
        n = 2
        while dest.exists() and sha256_file(dest) != digest:
            dest = self.papers_dir / ("%s-%d.pdf" % (base, n))
            n += 1
        if move:
            shutil.move(str(pdf), str(dest))
        else:
            shutil.copy2(str(pdf), str(dest))

        entry = {
            "sha256": digest,
            "path": str(dest.relative_to(self.wiki_root)),
            "pmid": pmid,
            "doi": normalize_doi(doi),
            "pmcid": pmcid,
            "title": title,
            "title_norm": normalize_title(title),
            "journal": journal,
            "year": year,
            "pages": pdf_pages(dest),
            "bytes": dest.stat().st_size,
            "added_at": utcnow(),
            "source_tier": source_tier,
            "access_route": access_route,
        }
        self.entries.append(entry)
        self.save()
        return entry, True


# ---------------------------------------------------------------- gitignore ----


def rewrite_gitignore(path: Path) -> dict:
    """Idempotently install the four-line library pattern (PLAN.md §2).

    Unrelated rules are preserved verbatim. Only a previous managed block and
    lines that would shadow it (`assets/`, `assets/*`, the four rules themselves)
    are removed before the block is appended.
    """
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = original.splitlines()

    kept: list[str] = []
    in_block = False
    removed: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == GITIGNORE_BEGIN:
            in_block = True
            continue
        if stripped == GITIGNORE_END:
            in_block = False
            continue
        if in_block:
            continue
        if stripped in GITIGNORE_SUPERSEDED:
            removed.append(stripped)
            continue
        kept.append(line)

    while kept and not kept[-1].strip():
        kept.pop()

    block = [GITIGNORE_BEGIN, *GITIGNORE_RULES, GITIGNORE_END]
    new_lines = kept + ([""] if kept else []) + block
    new_text = "\n".join(new_lines) + "\n"
    changed = new_text != original
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)
    return {"path": str(path), "changed": changed, "superseded_rules": removed}


# ------------------------------------------------------------------- corpus ----


def read_corpus(path: Path) -> list[dict]:
    records: list[dict] = []
    if not path.exists():
        return records
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def write_corpus(path: Path, records: list[dict]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)


def wiki_root_for_run(run_dir: Path) -> Path:
    """<wiki>/outputs/deep-research/<slug>/ -> <wiki> (PLAN.md §7)."""
    run_dir = Path(run_dir).expanduser().resolve()
    parents = run_dir.parents
    if len(parents) >= 3 and parents[0].name == "deep-research" and parents[1].name == "outputs":
        return parents[2]
    return run_dir


def missing_md_titles(run_dir: Path) -> list[dict]:
    """Parse `missing.md` quarantine entries back into {title, pmid, doi, pmcid}."""
    path = Path(run_dir) / "missing.md"
    if not path.exists():
        return []
    out: list[dict] = []
    current: dict | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if current:
                out.append(current)
            current = {"title": line[3:].strip(), "pmid": None, "doi": None, "pmcid": None,
                       "evidence_id": None}
            continue
        if current is None:
            continue
        m = re.match(r"^-\s*(evidence_id|PMID|DOI|PMCID)\s*:\s*(.+?)\s*$", line, re.I)
        if m:
            key, val = m.group(1).lower(), m.group(2).strip()
            if val in ("null", "-", ""):
                val = None
            current["evidence_id" if key == "evidence_id" else key] = val
    if current:
        out.append(current)
    return out


def unquarantine(run_dir: Path, evidence_id: str) -> bool:
    """Drop an evidence_id's block from missing.md once its full text has arrived."""
    path = Path(run_dir) / "missing.md"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    marker = "- evidence_id: %s" % evidence_id
    if marker not in text:
        return False
    blocks = re.split(r"(?m)^(?=## )", text)
    kept = [b for b in blocks if marker not in b]
    if len(kept) == len(blocks):
        return False
    path.write_text("".join(kept).rstrip("\n") + "\n", encoding="utf-8")
    return True


# -------------------------------------------------------------- ingest-inbox ---


def ingest_inbox(run_dir: Path, wiki_root: Path, corpus_path: Path, apply: bool = True) -> dict:
    run_dir = Path(run_dir).expanduser().resolve()
    inbox = run_dir / "inbox"
    lib = Library(wiki_root)
    lib.papers_dir.mkdir(parents=True, exist_ok=True)
    records = read_corpus(corpus_path)
    quarantined = [
        r for r in records
        if (r.get("fulltext") or {}).get("status") in (None, "missing", "abstract_only")
    ]
    candidates = quarantined or records
    quarantine_titles = missing_md_titles(run_dir)

    result = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utcnow(),
        "inbox": str(inbox),
        "corpus": str(corpus_path),
        "applied": apply,
        "ingested": [],
        "unmatched": [],
    }
    if not inbox.exists():
        result["error"] = "inbox does not exist: %s" % inbox
        return result

    by_eid = {evidence_id_of(r): r for r in records}
    updates: list[dict] = []

    for pdf in sorted(inbox.glob("*.pdf")) + sorted(inbox.glob("*.PDF")):
        digest = sha256_file(pdf)
        doi = doi_from_pdf(pdf)
        match, kind, score = (None, None, 0.0)
        if doi:
            for r in candidates:
                if normalize_doi(r.get("doi")) == doi:
                    match, kind, score = r, "doi_page1", 1.0
                    break
        if match is None:
            page1 = normalize_title(pdftotext(pdf, first=1, last=1))
            best, best_score = None, 0.0
            for r in candidates:
                tnorm = normalize_title(r.get("title"))
                if len(tnorm) < MIN_FUZZY_TITLE_CHARS:
                    continue
                s = title_in_text_ratio(tnorm, page1)
                if s > best_score:
                    best, best_score = r, s
            # also consider titles recovered from missing.md when the corpus is thin
            for q in quarantine_titles:
                tnorm = normalize_title(q.get("title"))
                if len(tnorm) < MIN_FUZZY_TITLE_CHARS:
                    continue
                s = title_in_text_ratio(tnorm, page1)
                if s > best_score:
                    eid = q.get("evidence_id") or (
                        "pmid:%s" % q["pmid"] if q.get("pmid") else None
                    )
                    cand = by_eid.get(eid) if eid else None
                    if cand is not None:
                        best, best_score = cand, s
            if best is not None and best_score >= FUZZY_PAGE_THRESHOLD:
                match, kind, score = best, "fuzzy_title", best_score

        if match is None:
            result["unmatched"].append(
                {"pdf": pdf.name, "sha256": digest, "doi_on_page1": doi,
                 "reason": "no DOI or fuzzy-title match >= %.2f" % FUZZY_PAGE_THRESHOLD}
            )
            continue

        entry, was_new = lib.add(
            pdf,
            pmid=match.get("pmid"),
            doi=match.get("doi") or doi,
            pmcid=match.get("pmcid"),
            title=match.get("title"),
            journal=match.get("journal"),
            year=(match.get("publication_date") or "")[:4] or None,
            source_tier=0,
            access_route="inbox_manual",
            stem=record_stem(match),
            move=True,
        )
        text, used_ocr = pdf_text_with_ocr(lib.entry_path(entry))
        text_dir = run_dir / "workspace" / "fulltext"
        text_dir.mkdir(parents=True, exist_ok=True)
        text_path = text_dir / ("%s.txt" % record_stem(match))
        text_path.write_text(text, encoding="utf-8")

        fulltext = {
            "status": "fulltext" if len(text.strip()) >= MIN_TEXT_CHARS else "abstract_only",
            "source_tier": 0,
            "access_route": "inbox_manual",
            "local_path": entry["path"],
            "sha256": entry["sha256"],
            "truncation_detected": False,
        }
        update = {
            "evidence_id": evidence_id_of(match),
            "matched_by": kind,
            "match_score": round(score, 3),
            "pdf": pdf.name,
            "library_path": entry["path"],
            "library_entry_new": was_new,
            "ocr_used": used_ocr,
            "text_path": str(text_path.relative_to(run_dir)),
            "fulltext": fulltext,
        }
        updates.append(update)
        result["ingested"].append(update)
        unquarantine(run_dir, update["evidence_id"])
        if apply:
            match["fulltext"] = fulltext

    if apply and updates and corpus_path.exists():
        write_corpus(corpus_path, records)

    updates_path = run_dir / "workspace" / "retrieve" / "inbox-updates.json"
    updates_path.parent.mkdir(parents=True, exist_ok=True)
    updates_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    result["updates_path"] = str(updates_path)
    return result


# ---------------------------------------------------------------------- CLI ----


def cmd_init(args) -> int:
    lib = Library(Path(args.wiki))
    print(json.dumps(lib.init(), indent=2))
    return 0


def cmd_lookup(args) -> int:
    lib = Library(Path(args.wiki))
    entry, kind, score = lib.lookup(
        doi=args.doi, pmid=args.pmid, pmcid=args.pmcid, title=args.title, sha256=args.sha256
    )
    print(json.dumps(
        {"matched": entry is not None, "match_kind": kind, "score": round(score, 3),
         "entry": entry},
        indent=2, ensure_ascii=False,
    ))
    return 0 if entry else 1


def cmd_add(args) -> int:
    lib = Library(Path(args.wiki))
    entry, was_new = lib.add(
        Path(args.pdf), pmid=args.pmid, doi=args.doi, pmcid=args.pmcid, title=args.title,
        journal=args.journal, year=args.year, source_tier=args.source_tier,
        access_route=args.access_route, move=args.move,
    )
    print(json.dumps({"new": was_new, "entry": entry}, indent=2, ensure_ascii=False))
    return 0


def cmd_ingest_inbox(args) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    wiki = Path(args.wiki).expanduser().resolve() if args.wiki else wiki_root_for_run(run_dir)
    corpus = Path(args.corpus) if args.corpus else run_dir / "corpus.jsonl"
    res = ingest_inbox(run_dir, wiki, corpus, apply=not args.no_apply)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


def cmd_list(args) -> int:
    lib = Library(Path(args.wiki))
    print(json.dumps(
        {"count": len(lib.entries), "entries": lib.entries[: args.limit]},
        indent=2, ensure_ascii=False,
    ))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="library.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create assets/papers/, index.json, rewrite .gitignore")
    s.add_argument("--wiki", required=True, help="wiki root directory")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("lookup", help="match by sha256/DOI/PMID/PMCID/fuzzy title")
    s.add_argument("--wiki", required=True)
    s.add_argument("--doi")
    s.add_argument("--pmid")
    s.add_argument("--pmcid")
    s.add_argument("--title")
    s.add_argument("--sha256")
    s.set_defaults(func=cmd_lookup)

    s = sub.add_parser("add", help="file a PDF into the library (sha256 dedupe)")
    s.add_argument("--wiki", required=True)
    s.add_argument("--pdf", required=True)
    s.add_argument("--pmid")
    s.add_argument("--doi")
    s.add_argument("--pmcid")
    s.add_argument("--title")
    s.add_argument("--journal")
    s.add_argument("--year")
    s.add_argument("--source-tier", type=int, dest="source_tier")
    s.add_argument("--access-route", dest="access_route")
    s.add_argument("--move", action="store_true", help="move instead of copy")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("ingest-inbox", help="match run inbox/ PDFs to quarantined records")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--wiki", help="wiki root (default: inferred from --run-dir)")
    s.add_argument("--corpus", help="corpus.jsonl (default: <run-dir>/corpus.jsonl)")
    s.add_argument("--no-apply", action="store_true",
                   help="emit updates only; do not rewrite corpus.jsonl")
    s.set_defaults(func=cmd_ingest_inbox)

    s = sub.add_parser("list", help="dump index entries")
    s.add_argument("--wiki", required=True)
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_list)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
