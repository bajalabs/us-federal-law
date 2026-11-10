"""eCFR full XML (DIV1..DIV9) -> divisions / articles / parts / annotations.

Sections (DIV8 TYPE=SECTION) and appendices (DIV9 TYPE=APPENDIX) are the
articles; subtitle, chapter, subchapter, part, subpart and subject group are the
divisions. Paragraph labels such as '(a)', '(1)', '(i)', '(A)' are resolved into
full paths like '(a)(1)(i)' with the CFR's standard level order.

Streaming: every article is processed at its end tag and then cleared.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import IO

from .common import collapse, key_suffix, local, numero_int, sha256_text, slugish

DIV_KIND = {
    "SUBTITLE": "subtitle",
    "CHAPTER": "chapter",
    "SUBCHAP": "subchapter",
    "SUBCHAPTER": "subchapter",
    "PART": "part",
    "SUBPART": "subpart",
    "SUBJGRP": "subject-group",
}
ARTICLE_TYPES = {"SECTION", "APPENDIX"}
# Editorial or structural elements whose words are not the section's normative text.
NOT_TEXT = {"HEAD", "CITA", "SECAUTH", "EDNOTE", "AUTH", "SOURCE", "PRTPAGE", "FTNT",
            "EFFDNOT", "STARS"}
BLOCKS = {"P", "FP", "FP-1", "FP-2", "FP-DASH", "FP1-2", "NOTE", "EXTRACT", "EXAMPLE",
          "GPOTABLE", "TABLE", "HD1", "HD2", "HD3", "HED", "PSPACE", "MATH", "IMG", "FRP",
          "SIG", "APPRO", "CODE", "DIV"}
INLINE = {"I", "E", "B", "SU", "FTREF", "AC", "SM", "FR", "LI", "GID", "PDF"}

DATE_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|June?|July?|Aug|Sept?|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),\s+(\d{4})\b")
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
          "sep": 9, "oct": 10, "nov": 11, "dec": 12}
LEAD_TOKEN = re.compile(r"^\(([0-9]+|[a-z]{1,3}|[A-Z]{1,2}|[ivxlc]+)\)\s*")
ROMAN = re.compile(r"^(?=[ivxlc]+$)c{0,3}(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")


def parse_dates(text: str) -> list[str]:
    out = []
    for mon, day, year in DATE_RE.findall(text or ""):
        try:
            out.append(datetime(int(year), MONTHS[mon[:3].lower()], int(day)).date().isoformat())
        except (KeyError, ValueError):
            continue
    return sorted(set(out))


def _text(el: ET.Element) -> str:
    out: list[str] = []

    def walk(e: ET.Element) -> None:
        if e.tag in NOT_TEXT:
            return
        if e.text:
            out.append(e.text)
        for ch in e:
            block = ch.tag not in INLINE
            if block:
                out.append(" ")
            walk(ch)
            if block:
                out.append(" ")
            if ch.tail:
                out.append(ch.tail)

    walk(el)
    return collapse("".join(out))


def _rows(el: ET.Element) -> list[str]:
    rows = []
    for tr in el.iter():
        if tr.tag in ("TR", "ROW"):
            cells = [_text(c) for c in tr if c.tag in ("TD", "TH", "ENT")]
            line = " | ".join(c for c in cells if c)
            if line:
                rows.append(line)
    return rows


class Labeler:
    """Turns a sequence of leading paragraph tokens into CFR paragraph paths.

    Level order: (a) lower letter, (1) number, (i) lower roman, (A) capital letter,
    italic (1), italic (i). Tokens that read both as a letter and as a roman numeral
    ('i', 'v', 'x') are letters when they continue a letter sequence, roman otherwise.
    """

    ORDER = ("alpha", "num", "roman", "ALPHA", "inum", "iroman")

    def __init__(self) -> None:
        self.stack: list[tuple[str, str]] = []

    def _types(self, tok: str, italic: bool) -> list[str]:
        if tok.isdigit():
            return ["inum", "num"] if italic else ["num", "inum"]
        if tok.isupper():
            return ["ALPHA"]
        cands = []
        if ROMAN.match(tok):
            cands += ["iroman", "roman"] if italic else ["roman", "iroman"]
        if len(set(tok)) == 1:
            cands.insert(0, "alpha")
        return cands or ["alpha"]

    @staticmethod
    def _prev_alpha(tok: str) -> str:
        c = tok[0]
        return (chr(ord(c) - 1) * len(tok)) if c != "a" else "z" * (len(tok) - 1)

    def push(self, tok: str, italic: bool = False) -> str:
        cands = self._types(tok, italic)
        chosen = cands[0]
        if "alpha" in cands and len(cands) > 1:
            # 'i', 'v', 'x' (and 'ii', ...) are letters only when they continue a
            # letter sequence: (h) -> (i); otherwise they are roman numerals.
            prev = dict(self.stack).get("alpha")
            chosen = "alpha" if prev is not None and prev == self._prev_alpha(tok) else cands[1]
        types = [t for t, _ in self.stack]
        if chosen in types:
            self.stack = self.stack[: types.index(chosen)]
        else:
            # a new, deeper level: drop anything at or below its rank
            rank = self.ORDER.index(chosen)
            self.stack = [(t, v) for t, v in self.stack if self.ORDER.index(t) < rank]
        self.stack.append((chosen, tok))
        return "".join(f"({v})" for _, v in self.stack)


def _leading_tokens(p: ET.Element, text: str) -> list[tuple[str, bool]]:
    """Paragraph tokens at the start of a P: '(a)', '(a)(1)', and the CFR habit
    '(a) <I>Heading.</I> (1) text', where (1) opens the first child paragraph."""
    toks: list[tuple[str, bool]] = []
    rest = text
    italic = False
    first = p[0] if len(p) else None
    if first is not None and first.tag in ("I", "E") and not (p.text or "").strip():
        italic = bool(LEAD_TOKEN.match(collapse("".join(first.itertext()))))
    while True:
        m = LEAD_TOKEN.match(rest)
        if not m:
            break
        toks.append((m.group(1), italic))
        rest = rest[m.end():]
        italic = False
    if toks:
        heads = [collapse("".join(ch.itertext())) for ch in p if ch.tag in ("I", "E")]
        for h in heads:
            if h and not h.startswith("(") and rest.startswith(h):
                after = rest[len(h):].lstrip()
                m = LEAD_TOKEN.match(after)
                if m:
                    toks.append((m.group(1), False))
                break
    return toks


def section_body(el: ET.Element) -> tuple[list[str], list[dict]]:
    paragraphs: list[str] = []
    parts: list[dict] = []
    lab = Labeler()
    current: dict | None = None
    for ch in el:
        tag = ch.tag
        if tag in NOT_TEXT:
            continue
        if tag in ("GPOTABLE", "TABLE"):
            rows = _rows(ch)
            paragraphs.extend(rows)
            if current is not None:
                current["chunks"].extend(rows)
            continue
        t = _text(ch)
        if not t:
            continue
        if tag in ("P", "FP", "FP-1", "FP-2", "FP-DASH", "FP1-2"):
            toks = _leading_tokens(ch, t)
            if toks:
                label = ""
                for tok, ital in toks:
                    label = lab.push(tok, ital)
                current = {"kind": "paragraph", "label": label, "chunks": [t]}
                parts.append(current)
                paragraphs.append(t)
                continue
        paragraphs.append(t)
        if current is not None:
            current["chunks"].append(t)
    out = []
    for i, p in enumerate(parts, 1):
        text = "\n\n".join(p["chunks"])
        out.append({"kind": p["kind"], "label": p["label"], "seq": i,
                    "text": text, "text_sha256": sha256_text(text)})
    return paragraphs, out


def _split_head(head: str) -> tuple[str, str | None]:
    """'PART 1—DEFINITIONS' -> ('Part 1', 'DEFINITIONS')."""
    h = collapse(head)
    m = re.match(r"^((?:SUBTITLE|CHAPTER|SUBCHAPTER|PART|SUBPART|Subtitle|Chapter|Subchapter|Part|"
                 r"Subpart)\s+[^—–]+?)\s*[—–]\s*(.*)$", h)
    if not m:
        return h, None
    first, _, rest = m.group(1).partition(" ")
    return f"{first.capitalize()} {rest}".strip(), (m.group(2).strip() or None)


def map_ecfr(source: IO[bytes] | str | Path) -> dict:
    divisions: list[dict] = []
    articles: list[dict] = []
    annotations: list[dict] = []
    meta: dict[str, object] = {}
    divs: list[tuple[ET.Element, dict]] = []
    in_article = 0
    for event, el in ET.iterparse(source, events=("start", "end")):
        typ = (el.get("TYPE") or "").upper()
        is_div = el.tag.startswith("DIV") and el.tag[3:].isdigit()
        if event == "start":
            if in_article:
                if is_div and typ in ARTICLE_TYPES:
                    in_article += 1
                continue
            if is_div and typ in ARTICLE_TYPES:
                in_article = 1
            elif is_div and typ in DIV_KIND:
                parent = divs[-1][1] if divs else None
                rec = {"seq": len(divisions) + 1, "parent_seq": parent["seq"] if parent else None,
                       "kind": DIV_KIND[typ], "ordinal_label": (el.get("N") or "").strip() or None,
                       "heading": None, "rubric": None, "depth": len(divs), "path": None}
                divisions.append(rec)
                divs.append((el, rec))
            elif el.tag == "VOLUME" and el.get("AMDDATE"):
                meta.setdefault("amended_date", el.get("AMDDATE"))
            continue
        # end
        if in_article:
            if is_div and typ in ARTICLE_TYPES:
                in_article -= 1
                if in_article == 0:
                    _article(el, typ, divs[-1][1] if divs else None, articles, annotations)
                    el.clear()
            continue
        if el.tag == "HEAD":
            if divs and _is_child(divs[-1][0], el):
                rec = divs[-1][1]
                if rec["heading"] is None:
                    if rec["kind"] == "subject-group":
                        rec["heading"] = collapse("".join(el.itertext()))
                    else:
                        rec["heading"], rec["rubric"] = _split_head("".join(el.itertext()))
            elif not divs and "title_heading" not in meta:
                meta["title_heading"] = collapse("".join(el.itertext()))
        elif el.tag in ("AUTH", "SOURCE", "EDNOTE") and divs and _is_child(divs[-1][0], el):
            t = _text_all(el)
            if t:
                annotations.append({"division_seq": divs[-1][1]["seq"],
                                    "target_kind": {"AUTH": "authority", "SOURCE": "source",
                                                    "EDNOTE": "editorial-note"}[el.tag],
                                    "action": None, "dates": parse_dates(t), "text": t})
        elif is_div and typ in DIV_KIND and divs and el is divs[-1][0]:
            _, rec = divs.pop()
            if not rec["heading"]:
                rec["heading"] = (rec["kind"].capitalize() + " " + (rec["ordinal_label"] or "")).strip()
            el.clear()
    by_seq = {d["seq"]: d for d in divisions}
    for d in divisions:
        chain, cur = [], d
        while cur:
            chain.append(f"{slugish(cur['kind'])}-{slugish(cur['ordinal_label'] or cur['heading'])}")
            cur = by_seq.get(cur["parent_seq"]) if cur["parent_seq"] else None
        d["path"] = "/".join(reversed(chain))
    return {"meta": meta, "divisions": divisions, "articles": articles,
            "annotations": annotations}


def _is_child(parent: ET.Element, el: ET.Element) -> bool:
    # HEAD/AUTH/SOURCE arrive before the division's later children are parsed;
    # a direct child is among the parent's children at that moment.
    for ch in parent:
        if ch is el:
            return True
    return False


def _text_all(el: ET.Element) -> str:
    return collapse(" ".join(el.itertext()))


def _article(el: ET.Element, typ: str, div: dict | None, articles: list[dict],
             annotations: list[dict]) -> None:
    n = (el.get("N") or "").strip()
    head = ""
    for ch in el:
        if ch.tag == "HEAD":
            head = collapse("".join(ch.itertext()))
            break
    if typ == "SECTION":
        key = re.sub(r"^§+\s*", "", n) or re.sub(r"^§+\s*", "", head.split()[1] if len(head.split()) > 1 else "")
    else:
        key = slugish(n or head, 80)
    paragraphs, parts = section_body(el)
    text = "\n\n".join(paragraphs)
    if not text:
        text = "[Reserved]" if re.search(r"\[reserved", head, re.I) else "[No text]"
    seq = len(articles) + 1
    last = None
    for ch in el:
        if ch.tag in ("CITA", "SECAUTH", "EDNOTE", "EFFDNOT"):
            t = _text_all(ch)
            if not t:
                continue
            dates = parse_dates(t)
            if ch.tag == "CITA" and dates:
                last = dates[-1]
            annotations.append({"article_seq": seq,
                                "target_kind": {"CITA": "citation", "SECAUTH": "authority",
                                                "EDNOTE": "editorial-note",
                                                "EFFDNOT": "effective-date-note"}[ch.tag],
                                "action": None, "dates": dates, "text": t})
    articles.append({
        "article_key": key,
        "qualified_key": None,
        "numero_int": numero_int(key) if typ == "SECTION" else None,
        "suffix": key_suffix(key) if typ == "SECTION" else None,
        "label": head or (f"§ {key}" if typ == "SECTION" else n),
        "seq": seq,
        "division_seq": div["seq"] if div else None,
        "status": "reserved" if re.search(r"\[reserved", head, re.I) else None,
        "text": text,
        "text_sha256": sha256_text(text),
        "char_count": len(text),
        "last_reform_date": last,
        "parts": parts,
        "refs": [],
    })
