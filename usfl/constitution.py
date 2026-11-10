"""The Constitution of the United States: GPO literal print -> articles and markdown.

Source: GPO CONAN 2022 literal-print granules (two PDFs), converted to text with
`pdftotext -layout` (poppler). Only this re-mapping step needs poppler: its output
is committed as data/constitution/constitution.json and constitution.md, and the
database build reads the JSON when the PDFs are not present.

Article keys:
  preamble · I-1 ... I-10 · II-1 ... · V · VI · VII · attestation ·
  amd-1 ... amd-27 (amendments without sections) · amd-14-1 ... (with sections)
Paragraphs inside an article section are its clauses: parts labelled 'cl. 1', 'cl. 2'.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .common import DATA_DIR, SOURCE_DIR, collapse, log, read_manifest, sha256_text

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9,
         "X": 10, "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15, "XVI": 16,
         "XVII": 17, "XVIII": 18, "XIX": 19, "XX": 20, "XXI": 21, "XXII": 22, "XXIII": 23,
         "XXIV": 24, "XXV": 25, "XXVI": 26, "XXVII": 27}
ARTICLE_RE = re.compile(r"^\s*ARTICLE\.?\s+([IVX]+)\.?\s*$")
SECTION_RE = re.compile(r"^\s*SECTION\.?\s+(\d+)\.?\s*$")
AMEND_RE = re.compile(r"^\s*AMENDMENT\s+([IVX]+)\s*$")
RUNNING_HEAD = re.compile(r"Literal Print\s*$")
PAGE_NO = re.compile(r"^\s*\d{1,3}\s*$")
BODY_END = re.compile(r"The Word, .the,. being|done in Convention")
ATTEST_RE = re.compile(r"(done in Convention.*?subscribed our Names,)", re.S)


def pdf_text(path: Path, layout: bool = True) -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        raise RuntimeError("pdftotext (poppler) is required to re-map the Constitution PDFs")
    args = [exe] + (["-layout"] if layout else []) + [str(path), "-"]
    r = subprocess.run(args, capture_output=True, text=True, check=True)
    return r.stdout


def _tokens(text: str, start: re.Pattern, end: re.Pattern | None = None) -> list[tuple[str, str]]:
    """Layout text -> [('article'|'section'|'amendment', value) | ('para', text)].

    A paragraph starts on an indented line; an unindented line continues the
    paragraph in progress (wrapped lines and text carried over a page break).
    """
    out: list[tuple[str, str]] = []
    started = False
    buf: list[str] = []

    def flush() -> None:
        if buf:
            t = ""
            for line in buf:
                line = line.strip()
                if t.endswith("-"):
                    t += line
                else:
                    t = f"{t} {line}" if t else line
            out.append(("para", collapse(t)))
            buf.clear()

    for raw in text.splitlines():
        line = raw.replace("\x0c", "").rstrip()
        if not started:
            if start.search(line):
                started = True
            else:
                continue
        if end is not None and end.search(line):
            break
        if not line.strip() or PAGE_NO.match(line) or RUNNING_HEAD.search(line):
            continue
        for kind, rx in (("article", ARTICLE_RE), ("section", SECTION_RE), ("amendment", AMEND_RE)):
            m = rx.match(line)
            if m:
                flush()
                out.append((kind, m.group(1)))
                break
        else:
            if line[:1] == " " and buf:
                flush()
            buf.append(line)
    flush()
    return out


def parse(body: str, amendments: str, attestation: str | None = None) -> dict:
    divisions: list[dict] = []
    articles: list[dict] = []

    def add_div(kind: str, heading: str, ordinal: str | None, parent: int | None,
                path: str) -> int:
        seq = len(divisions) + 1
        divisions.append({"seq": seq, "parent_seq": parent, "kind": kind,
                          "ordinal_label": ordinal, "heading": heading, "rubric": None,
                          "depth": 0 if parent is None else 1, "path": path})
        return seq

    def add_art(key: str, label: str, paras: list[str], div: int | None,
                numero: int | None) -> None:
        paras = [p for p in paras if p]
        if not paras:
            return
        text = "\n\n".join(paras)
        parts = []
        if len(paras) > 1:
            parts = [{"kind": "clause", "label": f"cl. {i}", "seq": i, "text": p,
                      "text_sha256": sha256_text(p)} for i, p in enumerate(paras, 1)]
        articles.append({"article_key": key, "qualified_key": None, "numero_int": numero,
                         "suffix": None, "label": label, "seq": len(articles) + 1,
                         "division_seq": div, "status": None, "text": text,
                         "text_sha256": sha256_text(text), "char_count": len(text),
                         "last_reform_date": None, "parts": parts, "refs": []})

    # the Constitution of 1787
    toks = _tokens(body, re.compile(r"WE THE PEOPLE"), BODY_END)
    art = sec = None
    art_div = None
    paras: list[str] = []
    preamble: list[str] = []

    def close() -> None:
        nonlocal paras
        if art is None:
            return
        if sec is None:
            add_art(art, f"Article {art}", paras, art_div, ROMAN[art])
        else:
            add_art(f"{art}-{sec}", f"Article {art}, Section {sec}", paras, art_div, int(sec))
        paras = []

    for kind, val in toks:
        if kind == "article":
            if art is None and preamble:
                add_art("preamble", "Preamble", preamble, None, None)
            close()
            art, sec = val, None
            art_div = add_div("article", f"Article {val}", val, None, f"article-{val.lower()}")
        elif kind == "section" and art is not None:
            close()
            sec = val
        elif kind == "para":
            (paras if art is not None else preamble).append(val)
    close()
    if attestation:
        m = ATTEST_RE.search(attestation)
        if m:
            add_art("attestation", "Attestation", [collapse(m.group(1))], None, None)

    # the amendments
    toks = _tokens(amendments, AMEND_RE)
    top = add_div("amendments", "Amendments", None, None, "amendments")
    amd = sec = None
    amd_div = None
    paras = []

    def close_amd() -> None:
        nonlocal paras
        if amd is None:
            return
        n = ROMAN[amd]
        if sec is None:
            add_art(f"amd-{n}", f"Amendment {amd}", paras, amd_div, n)
        else:
            add_art(f"amd-{n}-{sec}", f"Amendment {amd}, Section {sec}", paras, amd_div, int(sec))
        paras = []

    for kind, val in toks:
        if kind == "amendment":
            close_amd()
            amd, sec = val, None
            amd_div = add_div("amendment", f"Amendment {val}", val, top,
                              f"amendments/amendment-{val.lower()}")
        elif kind == "section" and amd is not None:
            close_amd()
            sec = val
        elif kind == "para" and amd is not None:
            paras.append(val)
    close_amd()
    return {"meta": {"title_heading": "The Constitution of the United States of America"},
            "divisions": divisions, "articles": articles, "annotations": []}


def to_markdown(mapped: dict, manifest: dict | None = None) -> str:
    divs = {d["seq"]: d for d in mapped["divisions"]}
    lines = ["# The Constitution of the United States of America", ""]
    if manifest:
        ed = manifest.get("edition", {})
        lines += [f"Source: {ed.get('name', '')} ({ed.get('package_id', '')}), "
                  "U.S. Government Publishing Office. Public domain (17 U.S.C. 105).", ""]
        for f in manifest.get("files", []):
            lines.append(f"- `{f['granule']}` {f['url']} sha256 `{f.get('sha256', '')}`")
        lines.append("")
    seen: set[int] = set()
    for a in mapped["articles"]:
        d = divs.get(a["division_seq"]) if a["division_seq"] else None
        chain = []
        while d and d["seq"] not in seen:
            chain.append(d)
            d = divs.get(d["parent_seq"]) if d["parent_seq"] else None
        for d in reversed(chain):
            seen.add(d["seq"])
            lines += [f"{'#' * (2 + d['depth'])} {d['heading']}", ""]
        div = divs.get(a["division_seq"]) if a["division_seq"] else None
        if a["article_key"] in ("preamble", "attestation"):
            lines += [f"## {a['label']}", ""]
        elif "Section" in a["label"]:
            depth = 3 + (div["depth"] if div else 0)
            lines += [f"{'#' * depth} {a['label'].split(', ')[-1]}", ""]
        for p in a["text"].split("\n\n"):
            lines += [p, ""]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m usfl.constitution",
                                 description="Re-map the Constitution PDFs into data/constitution/.")
    ap.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    a = ap.parse_args(argv)
    manifest = read_manifest("constitution")
    files = {f["key"]: a.source_dir / f["path"] for f in (manifest or {}).get("files", [])}
    if not files or not all(p.exists() for p in files.values()):
        log("Constitution PDFs not found; run: python3 -m usfl fetch constitution")
        return 1
    body = pdf_text(files["constitution"])
    mapped = parse(body, pdf_text(files["amendments"]), pdf_text(files["constitution"], layout=False))
    out = a.data_dir / "constitution"
    out.mkdir(parents=True, exist_ok=True)
    (out / "constitution.json").write_text(json.dumps(mapped, indent=1, ensure_ascii=False) + "\n",
                                           encoding="utf-8")
    (out / "constitution.md").write_text(to_markdown(mapped, manifest), encoding="utf-8")
    log(f"constitution: {len(mapped['divisions'])} divisions, {len(mapped['articles'])} articles "
        f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
