"""USLM XML -> divisions / articles / parts / annotations / references.

One streaming walker serves both United States Code titles (OLRC USLM 1.0) and
enrolled Public Laws (GPO USLM 2.x). Element local names are what matter; the
namespaces differ and are ignored.

Memory stays bounded: each article (a ``section`` or ``courtRule``) is processed
when its end tag arrives and then cleared, so a 200 MB title never sits whole in RAM.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import IO, Iterable

from .common import collapse, key_suffix, local, numero_int, sha256_text, slugish

# Hierarchy above the article, in USC and Public Laws.
DIVISION_TAGS = {
    "title", "subtitle", "chapter", "subchapter", "part", "subpart", "division",
    "subdivision", "article", "courtRules", "reorganizationPlans", "compiledAct",
}
ARTICLE_TAGS = {"section", "courtRule", "reorganizationPlan", "appropriations"}
# USLM identifier prefixes, used to synthesize an identifier when a Public Law
# section carries none (appropriations acts): division A, title I, sec. 101 -> dA/tI/s101
ID_PREFIX = {"division": "d", "title": "t", "subtitle": "st", "chapter": "ch",
             "subchapter": "sch", "part": "pt", "subpart": "spt"}
# Structural levels inside an article.
LEVEL_TAGS = (
    "subsection", "paragraph", "subparagraph", "clause", "subclause", "item",
    "subitem", "subsubitem",
)
LEVEL_SET = set(LEVEL_TAGS)
# Never normative text.
NOTE_TAGS = {"notes", "note", "sourceCredit", "toc", "layout", "footnote", "sidenote",
             "page", "meta", "editorialNote"}
SKIP_OUTSIDE_ARTICLE = {"notes", "note", "toc", "meta", "quotedContent", "layout"}
BLOCK_TAGS = {"p", "table", "quotedContent", "quotedText", "proviso", "def", "list"}
STATUS_WORDS = re.compile(
    r"^\s*\[?\s*(repealed|transferred|omitted|reserved|vacant|renumbered|redesignated|expired)\b",
    re.I)
NUM_TAIL = re.compile(r"[\s—–\-:.]+$")


# Inline elements join their neighbours without a space; everything else is a break.
INLINE_TAGS = {"ref", "i", "b", "u", "inline", "span", "date", "sup", "sub", "term",
               "shortTitle", "a", "del", "ins", "entity", "approvedDate", "em", "strong",
               "quotedText", "headingText", "marker", "fillIn", "checkBox", "footnoteRef"}


def _text(el: ET.Element, skip: set[str] = NOTE_TAGS) -> str:
    """Whitespace-collapsed text of an element, skipping editorial subtrees."""
    out: list[str] = []

    def walk(e: ET.Element) -> None:
        if local(e.tag) in skip:
            return
        if e.text:
            out.append(e.text)
        for ch in e:
            block = local(ch.tag) not in INLINE_TAGS
            if block:
                out.append(" ")
            walk(ch)
            if block:
                out.append(" ")
            if ch.tail:
                out.append(ch.tail)

    walk(el)
    return collapse("".join(out))


def _table_rows(el: ET.Element) -> list[str]:
    rows = []
    for tr in el.iter():
        if local(tr.tag) == "tr":
            cells = [_text(c) for c in tr if local(c.tag) in ("td", "th")]
            line = " | ".join(c for c in cells if c)
            if line:
                rows.append(line)
    return rows


def _paragraphs(el: ET.Element) -> list[str]:
    """Paragraph strings of a content-like element (content, chapeau, continuation)."""
    blocks = [ch for ch in el if local(ch.tag) in BLOCK_TAGS]
    if not blocks:
        t = _text(el)
        return [t] if t else []
    out: list[str] = []
    lead = collapse(el.text)
    if lead:
        out.append(lead)
    for ch in el:
        tag = local(ch.tag)
        if tag in NOTE_TAGS:
            pass
        elif tag == "table":
            out.extend(_table_rows(ch))
        else:
            t = _text(ch)
            if t:
                out.append(t)
        tail = collapse(ch.tail)
        if tail:
            out.append(tail)
    return out


def _num_label(el: ET.Element) -> str:
    for ch in el:
        if local(ch.tag) == "num":
            return collapse("".join(ch.itertext()))
    return ""


def _child_text(el: ET.Element, name: str) -> str:
    for ch in el:
        if local(ch.tag) == name:
            return _text(ch)
    return ""


def _dates(el: ET.Element) -> list[str]:
    out = []
    for d in el.iter():
        if local(d.tag) in ("date", "approvedDate"):
            v = d.get("date") or ""
            if re.match(r"^\d{4}-\d{2}-\d{2}$", v):
                out.append(v)
    return sorted(set(out))


def _refs(el: ET.Element, skip: set[str] = NOTE_TAGS) -> list[dict]:
    out: list[dict] = []

    def walk(e: ET.Element) -> None:
        if local(e.tag) in skip:
            return
        if local(e.tag) == "ref" and e.get("href"):
            out.append({"href": e.get("href"), "quote": collapse("".join(e.itertext()))})
        for ch in e:
            walk(ch)

    walk(el)
    return out


def article_body(el: ET.Element) -> tuple[list[str], list[dict]]:
    """Paragraphs of an article and its structural parts.

    Each level (subsection, paragraph, ...) contributes one part whose label is the
    full path ('(a)(1)(A)') and whose text is its own words: num, heading, chapeau or
    content, and any continuation that follows its children.
    """
    paragraphs: list[str] = []
    parts: list[dict] = []

    def walk(node: ET.Element, path: str, part: dict | None) -> None:
        for ch in node:
            tag = local(ch.tag)
            if tag in ("num", "heading", "subheading") or tag in NOTE_TAGS:
                continue
            if tag in LEVEL_SET:
                num = _num_label(ch)
                label = path + (num if num.startswith("(") else f"({num})" if num else "")
                heading = _child_text(ch, "heading")
                lead = collapse(" ".join(x for x in (num, heading) if x))
                me = {"kind": tag, "label": label or None, "chunks": []}
                parts.append(me)
                first = True
                for sub in ch:
                    st = local(sub.tag)
                    if st in ("content", "chapeau"):
                        ps = _paragraphs(sub)
                        if first and lead:
                            ps = [collapse(f"{lead} {ps[0]}")] + ps[1:] if ps else [lead]
                            first = False
                        paragraphs.extend(ps)
                        me["chunks"].extend(ps)
                if first and lead:
                    paragraphs.append(lead)
                    me["chunks"].append(lead)
                walk_children_levels(ch, label, me)
            elif tag in ("content", "chapeau", "continuation", "proviso") or tag in BLOCK_TAGS:
                if part is None:
                    ps = _paragraphs(ch) if tag not in BLOCK_TAGS else (
                        _table_rows(ch) if tag == "table" else [_text(ch)])
                    ps = [p for p in ps if p]
                    paragraphs.extend(ps)

    def walk_children_levels(level: ET.Element, path: str, me: dict) -> None:
        for sub in level:
            st = local(sub.tag)
            if st in LEVEL_SET:
                walk_one(sub, path)
            elif st == "continuation":
                ps = _paragraphs(sub)
                paragraphs.extend(ps)
                me["chunks"].extend(ps)

    def walk_one(level: ET.Element, path: str) -> None:
        holder = ET.Element("_")
        holder.append(level)
        walk(holder, path, None)

    walk(el, "", None)
    out_parts = []
    for i, p in enumerate(parts, 1):
        text = "\n\n".join(c for c in p["chunks"] if c)
        if not text:
            continue
        out_parts.append({"kind": p["kind"], "label": p["label"], "seq": i,
                          "text": text, "text_sha256": sha256_text(text)})
    return [p for p in paragraphs if p], out_parts


def _notes(section: ET.Element) -> list[dict]:
    out = []
    for notes in section:
        if local(notes.tag) != "notes":
            continue
        for note in notes:
            if local(note.tag) != "note" or note.get("role") == "crossHeading":
                continue
            heading = _child_text(note, "heading")
            body = [_text(ch, {"heading"} | (NOTE_TAGS - {"note"}))
                    for ch in note if local(ch.tag) != "heading"]
            text = "\n\n".join(b for b in body if b)
            if heading:
                text = f"{heading}\n\n{text}" if text else heading
            if text:
                out.append({"target_kind": "note", "action": note.get("topic"),
                            "dates": _dates(note), "text": text})
    return out


def _division_heading(kind: str, num: str) -> str:
    h = NUM_TAIL.sub("", num)
    if not h:
        return kind.capitalize()
    first, _, rest = h.partition(" ")
    if first.isupper() and len(first) > 1:
        first = first.capitalize()
    return f"{first} {rest}".strip()


class USLMWalker:
    """Streams one USLM document; see map_uslm()."""

    def __init__(self, law_identifier: str | None = None):
        self.law_identifier = law_identifier
        self.divisions: list[dict] = []
        self.articles: list[dict] = []
        self.annotations: list[dict] = []
        self.meta: dict[str, object] = {}
        self.main_text: list[str] = []
        self._approp = 0
        self._unnumbered = 0

    # identifier -> article key
    def article_key(self, el: ET.Element, open_divs: list[dict]) -> tuple[str, str]:
        tag = local(el.tag)
        if tag == "appropriations":
            self._approp += 1
            return f"appropriation-{self._approp}", ""
        ident = (el.get("identifier") or "").replace("\u2013", "-")   # keys use ASCII hyphens
        tail = re.sub(r"^/us/(?:usc/t[0-9a-zA-Z]+|pl/\d+/\d+|[a-z]+/[^/]+)/?", "", ident)
        if tag == "courtRule":
            m = re.search(r"courtRules/([^/]+)/rule([^/]+)$", ident)
            if m:
                return f"{m.group(1).lower()}-rule-{m.group(2)}", tail
        m = re.search(r"(?:^|/)s([^/]+)$", tail)
        if m:
            return m.group(1), tail
        num = el.find("./{*}num")
        val = (num.get("value") if num is not None else None) or _num_label(el)
        val = re.sub(r"^(?:§+|SEC\.|SECTION|Sec\.|Rule)\s*", "", val or "", flags=re.I).strip(" .")
        val = val.replace("\u2013", "-")
        if not tail and val and tag == "section":
            chain = [f"{ID_PREFIX[d['kind']]}{d['ordinal_label']}" for d in open_divs
                     if d["kind"] in ID_PREFIX and d.get("ordinal_label")]
            tail = "/".join(chain + [f"s{val}"])
        if not val and not tail:
            self._unnumbered += 1
            return f"unnumbered-{self._unnumbered}", ""
        return (val or tail), tail

    def run(self, source: IO[bytes] | str | Path) -> dict:
        stack: list[ET.Element] = []
        divs: list[tuple[ET.Element, dict]] = []
        skip = 0           # inside notes/toc/meta outside any article
        in_article = 0     # nesting depth of article elements (only the outermost counts)
        root_title_seen = False
        for event, el in ET.iterparse(source, events=("start", "end")):
            tag = local(el.tag)
            if event == "start":
                stack.append(el)
                if in_article:
                    if tag in ARTICLE_TAGS:
                        in_article += 1
                    continue
                if skip or tag in SKIP_OUTSIDE_ARTICLE:
                    if tag in SKIP_OUTSIDE_ARTICLE:
                        skip += 1
                    continue
                if tag in ARTICLE_TAGS:
                    in_article = 1
                elif tag == "appendix" and not divs and not root_title_seen and re.match(
                        r"^/us/usc/t[0-9a-zA-Z]+$", el.get("identifier") or ""):
                    root_title_seen = True       # a USC appendix document (5a, 11a, 18a, 28a)
                    self.meta["root"] = el
                elif tag in DIVISION_TAGS:
                    ident = el.get("identifier") or ""
                    if tag == "title" and not root_title_seen and (
                            not divs and re.match(r"^/us/usc/t[0-9a-zA-Z]+$", ident)):
                        root_title_seen = True
                        self.meta["root"] = el
                        continue
                    parent = divs[-1][1] if divs else None
                    rec = {
                        "seq": len(self.divisions) + 1,
                        "parent_seq": parent["seq"] if parent else None,
                        "kind": tag if tag not in ("courtRules",) else "court-rules",
                        "ordinal_label": None, "heading": None, "rubric": None,
                        "depth": len(divs),
                        "identifier": ident,
                        "path": None,
                    }
                    self.divisions.append(rec)
                    divs.append((el, rec))
                continue

            # end event
            stack.pop()
            parent = stack[-1] if stack else None
            if in_article:
                if tag in ARTICLE_TAGS:
                    in_article -= 1
                    if in_article == 0:
                        self._article(el, [r for _, r in divs])
                        el.clear()
                continue
            if tag in SKIP_OUTSIDE_ARTICLE:
                skip -= 1
                if tag == "meta":
                    self._meta(el)
                if skip == 0 and tag != "quotedContent":
                    el.clear()
                continue
            if skip:
                continue
            if tag in ("num", "heading") and parent is not None:
                if divs and parent is divs[-1][0]:
                    rec = divs[-1][1]
                    if tag == "num":
                        rec["ordinal_label"] = (el.get("value") or collapse("".join(el.itertext()))) or None
                        rec["heading"] = _division_heading(rec["kind"], collapse("".join(el.itertext())))
                    else:
                        rec["rubric"] = collapse("".join(el.itertext())) or None
                elif parent is self.meta.get("root"):
                    if tag == "heading":
                        self.meta["title_heading"] = collapse("".join(el.itertext()))
                    else:
                        self.meta["title_num"] = collapse("".join(el.itertext()))
            elif tag in DIVISION_TAGS and divs and el is divs[-1][0]:
                _, rec = divs.pop()
                if not rec["heading"]:
                    rec["heading"] = rec["rubric"] or rec["kind"].capitalize()
                    if rec["heading"] == rec["rubric"]:
                        rec["rubric"] = None
                el.clear()
            elif tag in ("longTitle", "officialTitle") and "official_title" not in self.meta:
                t = _text(el, NOTE_TAGS | {"docTitle"})
                if t:
                    self.meta["official_title"] = t
            elif tag == "main" and not self.articles:
                # joint resolutions and short acts with no sections: one article of text
                body = [p for ch in el if local(ch.tag) not in ("longTitle", "num", "heading")
                        for p in ([_text(ch)] if local(ch.tag) not in ("content",) else _paragraphs(ch))
                        if p]
                self.main_text = body
        # path = the chain of kind-ordinal slugs from the top division down
        by_seq = {d["seq"]: d for d in self.divisions}
        for d in self.divisions:
            chain = []
            cur = d
            while cur:
                chain.append(f"{slugish(cur['kind'])}-{slugish(cur['ordinal_label'] or cur['heading'])}")
                cur = by_seq.get(cur["parent_seq"]) if cur["parent_seq"] else None
            d["path"] = "/".join(reversed(chain))
            d.pop("identifier", None)
            d.pop("_parent_path", None)
        self.meta.pop("root", None)
        return {"meta": self.meta, "divisions": self.divisions,
                "articles": self.articles, "annotations": self.annotations,
                "main_text": self.main_text}

    def _meta(self, el: ET.Element) -> None:
        for ch in el:
            tag = local(ch.tag)
            val = collapse("".join(ch.itertext()))
            if tag in ("title", "docNumber", "docPublicationName", "approvedDate", "congress",
                       "publicPrivate", "date", "type"):
                self.meta.setdefault(tag, val)
            elif tag == "citableAs":
                self.meta.setdefault("citable_as", []).append(val)
            elif tag == "property" and ch.get("role"):
                self.meta[ch.get("role")] = val

    def _article(self, el: ET.Element, open_divs: list[dict]) -> None:
        div = open_divs[-1] if open_divs else None
        key, tail = self.article_key(el, open_divs)
        num = _num_label(el)
        heading = _child_text(el, "heading")
        if local(el.tag) == "appropriations":
            heading = " — ".join(x for x in (heading, _child_text(el, "subheading")) if x)
        label = collapse(" ".join(x for x in (num, heading) if x)) or (
            "Unnumbered text" if key.startswith("unnumbered-") else f"§ {key}")
        paragraphs, parts = article_body(el)
        if not paragraphs and local(el.tag) == "appropriations":
            self._approp -= 1   # a heading-only appropriations block is a heading, not text
            return
        text = "\n\n".join(paragraphs)
        status = el.get("status")
        if not text:
            if status or STATUS_WORDS.match(heading or ""):
                text = f"[{collapse(heading) or status.capitalize()}]"
            else:
                text = "[No text]"
        seq = len(self.articles) + 1
        credit_dates: list[str] = []
        for ch in el:
            if local(ch.tag) == "sourceCredit":
                t = _text(ch, set())
                credit_dates = _dates(ch)
                if t:
                    self.annotations.append({"article_seq": seq, "target_kind": "source-credit",
                                             "action": None, "dates": credit_dates, "text": t})
        for n in _notes(el):
            self.annotations.append({"article_seq": seq, **n})
        if seq == 1 and re.search(r"short title", label, re.I):
            # only section 1's short title names the whole Act; later ones name its parts
            st = next((e for e in el.iter() if local(e.tag) == "shortTitle"), None)
            if st is not None:
                t = collapse("".join(st.itertext())).strip("“”\"' ")
                if t:
                    self.meta["short_title"] = t
        self.articles.append({
            "article_key": key,
            "qualified_key": tail,
            "numero_int": numero_int(key),
            "suffix": key_suffix(key) if re.match(r"^\d", key or "") else None,
            "label": label,
            "seq": seq,
            "division_seq": div["seq"] if div else None,
            "status": status,
            "text": text,
            "text_sha256": sha256_text(text),
            "char_count": len(text),
            "last_reform_date": credit_dates[-1] if credit_dates else None,
            "parts": parts,
            "refs": _refs(el),
        })


def map_uslm(source: IO[bytes] | str | Path) -> dict:
    """Map one USLM document (a USC title or a Public Law)."""
    return USLMWalker().run(source)


def map_uslm_bytes(data: bytes) -> dict:
    return map_uslm(io.BytesIO(data))


def disambiguate_keys(articles: Iterable[dict]) -> None:
    """Make article keys unique within one law.

    Public Laws repeat section numbers across divisions (Division A sec. 101,
    Division B sec. 101) and USC appendices compile several Acts with their own
    section 1. When a key collides and the colliding articles carry distinct USLM
    identifiers, every one of them takes its identifier tail ('dA/tI/s101',
    'pl/96/456/s1'). Anything still colliding (a handful of sections the Code itself
    numbers twice) keeps the plain key on the first occurrence and gets '~2', '~3'
    on the later ones.
    """
    arts = list(articles)
    groups: dict[str, list[dict]] = {}
    for a in arts:
        groups.setdefault(a["article_key"], []).append(a)
    for key, group in groups.items():
        if len(group) < 2:
            continue
        quals = [a.get("qualified_key") or "" for a in group]
        if all(quals) and len(set(quals)) == len(quals) and not all(
                q == f"s{key}" for q in quals):
            for a in group:
                a["article_key"] = a["qualified_key"]
    seen: dict[str, int] = {}
    for a in arts:
        k = a["article_key"]
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1:
            a["article_key"] = f"{k}~{seen[k]}"
