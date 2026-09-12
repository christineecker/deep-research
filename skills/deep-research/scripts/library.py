#!/usr/bin/env python3
"""library.py — shared cross-run PDF library at <wiki-root>/assets/papers/.

Rung 0 of the full-text acquisition ladder (`SKILL.md` "Pipeline") plus the inbox resume loop.

Subcommands
-----------
  init          create assets/papers/ + index.json, rewrite the wiki .gitignore
  lookup        match a record by DOI / PMID / fuzzy title
  add           file a PDF into the library (sha256 content dedupe)
  ingest-inbox  match every PDF in <run-dir>/inbox/ to a quarantined corpus record
  list          dump index entries
  figures       crop captioned figure images out of a PDF (caption anchoring)

Evidence kernel: `ingest-inbox` registers each matched PDF's extracted text into the run's
snapshot store (`scripts/store.py`, references/schema.md §10-§11) with
`origin: user-supplied-pdf`, and writes a `local_pdf` event carrying the asset triple
`{path, sha256, bytes}`. That event plus the matching hash is what makes the fresh-fetch
exception work for a manually supplied paper (`references/evidence-kernel.md`) —
there is nothing to re-fetch, so the immutable hash-checked local file *is* the fresh source.

PDFs are never copied per run (D3/R13): they stay in `<wiki>/assets/papers/` and the snapshot
records the wiki-root-relative path plus the sha256. Registration is additive and best-effort —
a store failure is logged to `<run-dir>/engine.log` and never aborts a successful ingest.

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
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import utcnow, wiki_root_for_run  # noqa: E402  (sibling module, stdlib-only)
try:  # the evidence kernel is additive: the library must work without it
    import store  # noqa: E402  (sibling module, stdlib-only)
except Exception:  # pragma: no cover - store.py is a sibling and always present
    store = None

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


def entry_is_preprint(entry: dict | None) -> bool:
    """Read an index entry's preprint flag.

    The field is additive: entries written before it existed simply lack the key and
    read back as `False`. The index is never rewritten just to backfill the default —
    it is only persisted when something else already changed it.
    """
    return bool((entry or {}).get("is_preprint"))


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


def pdf_text_with_ocr(pdf: Path, max_ocr_pages: int = 30) -> tuple[str, bool]:
    """Return (text, used_ocr). OCR only when pdftotext yields < MIN_TEXT_CHARS."""
    text = pdftotext(pdf)
    if len(text.strip()) >= MIN_TEXT_CHARS:
        return text, False
    ocr = ocr_pdf(pdf, max_pages=max_ocr_pages)
    if len(ocr.strip()) > len(text.strip()):
        return ocr, True
    return text, False


def doi_from_pdf(pdf: Path) -> str | None:
    """DOI regex over page-1 text (pdfinfo metadata rarely carries it; `SKILL.md` "Pipeline")."""
    text = pdftotext(pdf, first=1, last=1)
    if len(text.strip()) < MIN_TEXT_CHARS:
        text = (text or "") + "\n" + ocr_pdf(pdf, max_pages=1)
    m = DOI_RE.search(text or "")
    return normalize_doi(m.group(0)) if m else None


# ----------------------------------------------------------------- figures -----
#
# Figures are recovered by *caption anchoring*, not by pulling image objects out of
# the PDF. `pdfimages` only sees embedded bitmaps, so it misses every vector chart
# (which is most of them), splits tiled figures into fragments, and returns logos
# and rules alongside real content, with no captions and no figure numbers.
#
# Instead: `pdftotext -bbox` gives every word's rectangle. A line that *starts* with
# "Figure 3" is a caption (an in-text mention like "as shown in Figure 3" never
# does). The figure is then the whitespace band directly above that caption, bounded
# by the nearest content above it within the same column; `pdftoppm -x/-y/-W/-H`
# renders exactly that band.
#
# All poppler, no new dependencies, and each figure carries its label, caption text
# and page. Vector and bitmap figures come out the same way, because the page is
# rasterised rather than dissected. Expect this to land most but not all figures;
# see `extract_figures` for the known misses.

# A caption line must *start* with one of these.
#
# Tables are deliberately absent: their caption sits above a body that is itself
# text, so the whitespace geometry that isolates a figure finds nothing to bound,
# and the caption block runs straight into the first rows. Tables are already
# captured verbatim by the text layer, which is the better representation of them
# anyway — cropping them to pixels would lose the cell values to search.
CAPTION_RE = re.compile(
    r"^(?P<kind>Fig(?:ure|s?\.)?|Scheme|Chart|Exhibit|Plate)\s*"
    r"(?P<number>\d{1,3}[A-Za-z]?|[IVXLC]{1,6})\s*[.:)–-]?(?:\s|$)",
    re.IGNORECASE,
)
FIGURE_MIN_POINTS = 40.0     # a band thinner than this is a stray gap, not a figure
FIGURE_PAD_POINTS = 4.0      # breathing room around a detected band
CAPTION_MAX_LINES = 6        # captions longer than this have run into body text
LINE_OVERLAP_RATIO = 0.5     # words share a line when their y-spans overlap this much
COLUMN_MIN_LINES = 3         # lines needed on each side before a gutter means two columns
COLUMN_CROSSING_RATIO = 0.15  # share of lines allowed to straddle a genuine gutter
BLANK_INK_FRACTION = 0.004   # a crop with less ink than this is empty page, not a figure
INK_PROBE_DPI = 20           # cheap greyscale probe resolution for the blank check


class _BBoxParser(HTMLParser):
    """Reads `pdftotext -bbox` XHTML into pages of word rectangles.

    html.parser rather than ElementTree: the output carries an XHTML doctype and
    may contain named entities, both of which ElementTree rejects.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pages: list[dict] = []
        self._rect: tuple[float, float, float, float] | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        # html.parser lower-cases attribute names, so the source's xMin reads as xmin.
        a = {k.lower(): v for k, v in attrs}
        if tag == "page":
            self.pages.append({
                "width": _as_float(a.get("width")),
                "height": _as_float(a.get("height")),
                "words": [],
            })
        elif tag == "word" and self.pages:
            keys = ("xmin", "ymin", "xmax", "ymax")
            if all(a.get(k) is not None for k in keys):
                self._rect = tuple(_as_float(a[k]) for k in keys)  # type: ignore[assignment]
                self._buf = []

    def handle_data(self, data: str) -> None:
        if self._rect is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "word" and self._rect is not None:
            text = "".join(self._buf).strip()
            if text:
                x0, y0, x1, y1 = self._rect
                self.pages[-1]["words"].append(
                    {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": text}
                )
            self._rect = None
            self._buf = []


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def bbox_pages(pdf: Path) -> list[dict]:
    """`pdftotext -bbox` -> [{width, height, words:[{x0,y0,x1,y1,text}]}] in points.

    Empty when poppler is missing or the PDF has no text layer (a pure scan). Origin
    is top-left with y increasing downwards, matching `pdftoppm`'s crop coordinates.
    """
    if not shutil.which("pdftotext"):
        return []
    try:
        proc = _run(["pdftotext", "-bbox", str(pdf), "-"], timeout=300)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    parser = _BBoxParser()
    try:
        parser.feed(proc.stdout)
        parser.close()
    except Exception:  # malformed output is a miss, never a crash
        return []
    return parser.pages


def _group_lines(words: list[dict]) -> list[dict]:
    """Cluster words into text lines by vertical overlap, ordered top-to-bottom."""
    lines: list[dict] = []
    for w in sorted(words, key=lambda w: (w["y0"], w["x0"])):
        current = lines[-1] if lines else None
        if current is not None and _shares_line(current, w):
            current["words"].append(w)
            current["x0"] = min(current["x0"], w["x0"])
            current["x1"] = max(current["x1"], w["x1"])
            current["y0"] = min(current["y0"], w["y0"])
            current["y1"] = max(current["y1"], w["y1"])
        else:
            lines.append({"x0": w["x0"], "y0": w["y0"], "x1": w["x1"], "y1": w["y1"],
                          "words": [w]})
    for line in lines:
        line["words"].sort(key=lambda w: w["x0"])
        line["text"] = " ".join(w["text"] for w in line["words"])
    return lines


def _shares_line(line: dict, word: dict) -> bool:
    overlap = min(line["y1"], word["y1"]) - max(line["y0"], word["y0"])
    shortest = min(line["y1"] - line["y0"], word["y1"] - word["y0"])
    return shortest > 0 and (overlap / shortest) >= LINE_OVERLAP_RATIO


def _detect_columns(page: dict, lines: list[dict]) -> list[tuple[float, float]]:
    """Column x-ranges: two entries for a two-column layout, one otherwise.

    The split is the x in the middle of the page crossed by the fewest lines,
    rather than the widest fully clear band — a two-column page nearly always
    carries a few genuinely full-width lines (title, running head, a spanning
    figure), and demanding a perfectly clear gutter lets any one of them hide
    the layout. Two conditions then have to hold together: almost nothing
    crosses the split, and both sides are properly populated. The second is
    what stops a single column of ragged-right text, which trivially satisfies
    the first, from being read as two.
    """
    width = page["width"]
    if width <= 0 or len(lines) < COLUMN_MIN_LINES * 2:
        return [(0.0, max(width, 1.0))]

    best_split, best_crossings = None, None
    x = width * 0.35
    while x <= width * 0.65:
        crossings = sum(1 for ln in lines if ln["x0"] < x < ln["x1"])
        if best_crossings is None or crossings < best_crossings:
            best_split, best_crossings = x, crossings
        x += 2.0
    if best_split is None:
        return [(0.0, width)]

    if best_crossings > max(1, int(len(lines) * COLUMN_CROSSING_RATIO)):
        return [(0.0, width)]
    left = sum(1 for ln in lines if ln["x1"] <= best_split)
    right = sum(1 for ln in lines if ln["x0"] >= best_split)
    if min(left, right) < COLUMN_MIN_LINES:
        return [(0.0, width)]
    return [(0.0, best_split), (best_split, width)]


def _column_of(line: dict, columns: list[tuple[float, float]]) -> int:
    centre = (line["x0"] + line["x1"]) / 2.0
    for i, (x0, x1) in enumerate(columns):
        if x0 <= centre < x1:
            return i
    return len(columns) - 1


def _caption_block(anchor_index: int, column_lines: list[dict]) -> tuple[str, float]:
    """Caption text starting at the anchor line, and the y where the caption ends.

    Continuation stops at the first line separated by more than ordinary leading,
    which is what keeps a two-line caption from swallowing the paragraph under it.
    """
    anchor = column_lines[anchor_index]
    height = max(anchor["y1"] - anchor["y0"], 1.0)
    parts = [anchor["text"]]
    bottom = anchor["y1"]
    for line in column_lines[anchor_index + 1: anchor_index + CAPTION_MAX_LINES]:
        if (line["y0"] - bottom) > height * 1.6 or CAPTION_RE.match(line["text"]):
            break
        parts.append(line["text"])
        bottom = line["y1"]
    return " ".join(parts), bottom


def _region_above(anchor: dict, column: tuple[float, float], lines: list[dict]) -> tuple | None:
    """Whitespace band above a figure caption, bounded by the nearest content above."""
    blockers = [ln["y1"] for ln in lines
                if ln["y1"] <= anchor["y0"] + 1.0 and _overlaps_x(ln, column)]
    top = max(blockers) if blockers else 0.0
    if (anchor["y0"] - top) < FIGURE_MIN_POINTS:
        return None
    return (column[0], top + FIGURE_PAD_POINTS,
            column[1], anchor["y0"] - FIGURE_PAD_POINTS)


def _overlaps_x(line: dict, column: tuple[float, float]) -> bool:
    return line["x1"] > column[0] and line["x0"] < column[1]


def _crop_args(box: tuple, dpi: int) -> list[str]:
    scale = dpi / 72.0
    x0, y0, x1, y1 = box
    return ["-x", str(max(0, int(x0 * scale))), "-y", str(max(0, int(y0 * scale))),
            "-W", str(max(1, int(round((x1 - x0) * scale)))),
            "-H", str(max(1, int(round((y1 - y0) * scale))))]


def _render_region(pdf: Path, page_no: int, box: tuple, dpi: int,
                   gray: bool = False) -> bytes | None:
    """Rasterise one page region. PNG, or raw PGM when `gray` (for the ink probe)."""
    if not shutil.which("pdftoppm"):
        return None
    cmd = ["pdftoppm", "-r", str(dpi), "-f", str(page_no), "-l", str(page_no)]
    cmd += ["-gray"] if gray else ["-png"]
    cmd += _crop_args(box, dpi)
    with tempfile.TemporaryDirectory() as td:
        stem = os.path.join(td, "crop")
        try:
            proc = _run(cmd + [str(pdf), stem], timeout=300)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        found = sorted(Path(td).glob("crop*"))
        return found[0].read_bytes() if found else None


def _ink_fraction(pgm: bytes) -> float:
    """Share of non-white pixels in a binary (P5) PGM. 0.0 when unparseable."""
    if not pgm.startswith(b"P5"):
        return 0.0
    fields: list[bytes] = []
    pos = 2
    while len(fields) < 3 and pos < len(pgm):
        while pos < len(pgm) and pgm[pos: pos + 1].isspace():
            pos += 1
        if pgm[pos: pos + 1] == b"#":
            while pos < len(pgm) and pgm[pos: pos + 1] != b"\n":
                pos += 1
            continue
        start = pos
        while pos < len(pgm) and not pgm[pos: pos + 1].isspace():
            pos += 1
        fields.append(pgm[start:pos])
    if len(fields) < 3:
        return 0.0
    try:
        maxval = int(fields[2])
    except ValueError:
        return 0.0
    if maxval > 255:  # 16-bit PGM; not worth decoding for a blank check
        return 1.0
    pixels = pgm[pos + 1:]
    if not pixels:
        return 0.0
    threshold = int(maxval * 0.98)
    return sum(1 for b in pixels if b < threshold) / len(pixels)


def _looks_blank(pdf: Path, page_no: int, box: tuple) -> bool:
    probe = _render_region(pdf, page_no, box, INK_PROBE_DPI, gray=True)
    if probe is None:
        return False  # cannot tell; keep the candidate rather than drop it silently
    return _ink_fraction(probe) < BLANK_INK_FRACTION


def _normalise_kind(raw: str) -> str:
    lowered = raw.lower().rstrip(".")
    if lowered.startswith("fig"):
        return "figure"
    return lowered


def extract_figures(
    pdf: Path,
    *,
    dpi: int = 300,
    pages: list[int] | None = None,
    max_figures: int = 100,
    out_dir: Path | None = None,
) -> list[dict]:
    """Recover captioned figure images from a PDF by caption anchoring.

    Returns one dict per figure, ordered by page then position:

        {page, kind, label, number, caption, bbox, dpi, png_bytes, path?}

    `bbox` is `[x0, y0, x1, y1]` in PDF points, top-left origin. `path` is present
    only when `out_dir` is given, in which case the PNG is also written there.

    Known misses, in rough order of how often they bite:
      * scanned PDFs with no text layer — no anchors exist; OCR first
      * figures whose caption sits on the facing page
      * figures spanning both columns of a two-column layout, when a full-width
        caption is mis-assigned to one column
      * captions typeset in-line with body text rather than on their own line
    Candidates that render to blank page area are dropped, so misdetection tends
    towards missing figures rather than emitting junk.
    """
    results: list[dict] = []
    for page_index, page in enumerate(bbox_pages(pdf), start=1):
        if pages is not None and page_index not in pages:
            continue
        lines = _group_lines(page["words"])
        if not lines:
            continue
        columns = _detect_columns(page, lines)
        by_column: dict[int, list[dict]] = {}
        for line in lines:
            by_column.setdefault(_column_of(line, columns), []).append(line)

        for column_index, column_lines in by_column.items():
            column = columns[column_index]
            for i, line in enumerate(column_lines):
                match = CAPTION_RE.match(line["text"])
                if match is None:
                    continue
                kind = _normalise_kind(match.group("kind"))
                caption, _ = _caption_block(i, column_lines)
                box = _region_above(line, column, lines)
                if box is None or _looks_blank(pdf, page_index, box):
                    continue
                png = _render_region(pdf, page_index, box, dpi)
                if not png:
                    continue
                results.append({
                    "page": page_index,
                    "kind": kind,
                    "label": "%s %s" % (kind.capitalize(), match.group("number")),
                    "number": match.group("number"),
                    "caption": caption,
                    "bbox": [round(v, 2) for v in box],
                    "dpi": dpi,
                    "png_bytes": png,
                })
                if len(results) >= max_figures:
                    break
            if len(results) >= max_figures:
                break
        if len(results) >= max_figures:
            break

    results.sort(key=lambda r: (r["page"], r["bbox"][1]))
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for n, fig in enumerate(results, start=1):
            path = out_dir / ("p%03d-%s-%02d.png" % (fig["page"], fig["kind"], n))
            path.write_bytes(fig["png_bytes"])
            fig["path"] = str(path)
    return results


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
        is_preprint: bool = False,
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
            # preprint-ness only ever ratchets up: an explicit true from the caller
            # fills in a missing or false flag, a false never clears a stored true.
            if is_preprint and not entry_is_preprint(existing):
                existing["is_preprint"] = True
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
            "is_preprint": bool(is_preprint),
        }
        self.entries.append(entry)
        self.save()
        return entry, True


# ---------------------------------------------------------------- gitignore ----


def rewrite_gitignore(path: Path) -> dict:
    """Idempotently install the four-line library pattern (`SKILL.md` "Scripts").

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


# ---------------------------------------------------------- evidence kernel ---


def log(run_dir: Path, msg: str) -> None:
    path = Path(run_dir) / "engine.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s library.py %s\n" % (utcnow(), msg))
    except OSError:
        pass


def register_local_pdf(run_dir: Path, wiki_root: Path, rec: dict, entry: dict, text: str,
                       *, actor: str = "main") -> str | None:
    """Register a library PDF's text as a snapshot and log its `local_pdf` event.

    Returns the `source_id`, or None when nothing was registered.

    * `origin` is `user-supplied-pdf` and the snapshot's `asset` is the triple
      `{path, sha256, bytes}` with a **wiki-root-relative** path (R13). The PDF is not
      copied into the run; `<run>/sources/` holds only `src-*.json`.
    * The bytes on disk are re-hashed here and must match `entry["sha256"]`. Only then is
      a `local_pdf` event written with `fresh: true` — that event plus the matching hash
      is the user-supplied-PDF exception to the fresh-fetch rule (schema.md §11). A
      mismatch falls back to a plain `register` event, which is never fresh (R22).
    * `access` follows R21: a PDF that yielded usable text is `full_text`, one that did
      not is `abstract`, mirroring `fulltext.status`.
    * Best-effort: any store failure is logged to `engine.log` and swallowed, so an
      ingest that already succeeded is never lost to a kernel problem. `<run>/sources/`
      is created lazily on the first registration.
    """
    if store is None:
        return None
    text = text or ""
    if not text.strip():
        return None
    wiki_root = Path(wiki_root).expanduser().resolve()
    rel = Path(entry["path"]).as_posix()
    pdf = wiki_root / rel
    try:
        digest = sha256_file(pdf)
        nbytes = pdf.stat().st_size
    except OSError as exc:
        log(run_dir, "cannot hash %s for registration: %r" % (rel, exc))
        return None
    matched = digest == entry.get("sha256")
    if not matched:
        log(run_dir, "asset hash mismatch for %s (%s on disk, %s indexed); registering "
                     "without a local_pdf event" % (rel, digest[:16],
                                                    str(entry.get("sha256"))[:16]))
    # R21: usable text is `full_text`, unusable text is `abstract`. A preprint — flagged
    # by the corpus record or by the library entry, an explicit true from either winning —
    # is `preprint` instead, so a cached preprint is never mistaken for a published paper.
    if len(text.strip()) < MIN_TEXT_CHARS:
        access = "abstract"
    elif bool(rec.get("is_preprint")) or entry_is_preprint(entry):
        access = "preprint"
    else:
        access = "full_text"
    paper = {
        "pmid": rec.get("pmid") or None,
        "doi": normalize_doi(rec.get("doi")) or normalize_doi(entry.get("doi")),
        "pmcid": rec.get("pmcid") or None,
    }
    try:
        snap = store.write_snapshot(
            run_dir,
            url="file:///" + rel,
            text=text,
            title=rec.get("title") or entry.get("title"),
            access=access,
            origin="user-supplied-pdf",
            paper=paper,
            asset={"path": rel, "sha256": digest, "bytes": nbytes},
            event_type="local_pdf" if matched else "register",
            fresh=matched,
            actor=actor,
            detail=("user-supplied pdf %s; %d chars, %d bytes, asset bytes hashed and %s"
                    % (rel, len(text), nbytes, "matched" if matched else "MISMATCHED")),
        )
    except Exception as exc:  # a kernel problem must never lose a successful ingest
        log(run_dir, "snapshot registration failed for %s: %r" % (rel, exc))
        return None
    source_id = snap["source_id"]
    ids = rec.setdefault("source_ids", [])
    if source_id not in ids:
        ids.append(source_id)
    log(run_dir, "%s registered %s (access=%s, origin=user-supplied-pdf%s)"
        % (evidence_id_of(rec), source_id, access, ", local_pdf fresh" if matched else ""))
    return source_id


# -------------------------------------------------------------- ingest-inbox ---


def ingest_inbox(run_dir: Path, wiki_root: Path, corpus_path: Path, apply: bool = True,
                 register: bool = True) -> dict:
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
        "registered": register,
        "sources_dir": str(run_dir / "sources") if register else None,
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
            is_preprint=bool(match.get("is_preprint")),
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
        source_id = register_local_pdf(run_dir, wiki_root, match, entry, text) \
            if register else None

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
            "source_id": source_id,
            "source_ids": list(match.get("source_ids") or []),
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
        access_route=args.access_route, is_preprint=args.is_preprint, move=args.move,
    )
    print(json.dumps({"new": was_new, "entry": entry}, indent=2, ensure_ascii=False))
    return 0


def cmd_ingest_inbox(args) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    wiki = Path(args.wiki).expanduser().resolve() if args.wiki else wiki_root_for_run(run_dir)
    corpus = Path(args.corpus) if args.corpus else run_dir / "corpus.jsonl"
    res = ingest_inbox(run_dir, wiki, corpus, apply=not args.no_apply,
                       register=not args.no_register)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


def cmd_list(args) -> int:
    lib = Library(Path(args.wiki))
    print(json.dumps(
        {"count": len(lib.entries), "entries": lib.entries[: args.limit]},
        indent=2, ensure_ascii=False,
    ))
    return 0


def cmd_figures(args) -> int:
    figures = extract_figures(
        Path(args.pdf).expanduser(), dpi=args.dpi,
        max_figures=args.limit, out_dir=Path(args.out) if args.out else None,
    )
    print(json.dumps(
        {"count": len(figures),
         "figures": [{k: v for k, v in f.items() if k != "png_bytes"} for f in figures]},
        indent=2, ensure_ascii=False,
    ))
    return 0 if figures else 1


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
    s.add_argument("--is-preprint", action="store_true", dest="is_preprint",
                   help="the PDF is a preprint; snapshots of it get access=preprint")
    s.add_argument("--move", action="store_true", help="move instead of copy")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("ingest-inbox", help="match run inbox/ PDFs to quarantined records")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--wiki", help="wiki root (default: inferred from --run-dir)")
    s.add_argument("--corpus", help="corpus.jsonl (default: <run-dir>/corpus.jsonl)")
    s.add_argument("--no-apply", action="store_true",
                   help="emit updates only; do not rewrite corpus.jsonl")
    s.add_argument("--no-register", action="store_true", dest="no_register",
                   help="do not register ingested PDFs into <run-dir>/sources/ (kernel off)")
    s.set_defaults(func=cmd_ingest_inbox)

    s = sub.add_parser("list", help="dump index entries")
    s.add_argument("--wiki", required=True)
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("figures", help="extract captioned figure images from a PDF")
    s.add_argument("--pdf", required=True)
    s.add_argument("--out", help="write PNGs to this directory")
    s.add_argument("--dpi", type=int, default=300)
    s.add_argument("--limit", type=int, default=100, help="max figures to extract")
    s.set_defaults(func=cmd_figures)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
