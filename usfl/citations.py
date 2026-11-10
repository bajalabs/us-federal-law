"""Citations: USLM link targets and plain-text citation patterns -> target law + key.

A target is a tuple (law_slug, article_key or None). The build resolves slugs to
law ids and checks the key exists; anything outside this corpus is kept with
ref_kind 'unresolved' and its quote, so the evidence is never lost.
"""
from __future__ import annotations

import re

# ── USLM hrefs ──────────────────────────────────────────────────────────────

HREF_USC = re.compile(r"^/us/usc/t([0-9]+a?)(?:/s([^/]+))?")
HREF_PL = re.compile(r"^/us/pl/(\d+)/(\d+)(?:/s([^/]+))?")
HREF_CFR = re.compile(r"^/us/cfr/t(\d+)(?:/(?:s|pt)([^/]+))?")
HREF_CONST = re.compile(r"^/us/const")


def target_from_href(href: str) -> tuple[str, str | None] | None:
    """'/us/usc/t42/s1983/a' -> ('usc-42', '1983'); '/us/pl/118/5/s2' -> ('plaw-118-5', '2')."""
    href = (href or "").strip().replace("\u2013", "-")
    m = HREF_USC.match(href)
    if m:
        return f"usc-{m.group(1).lower()}", m.group(2)
    m = HREF_PL.match(href)
    if m:
        return f"plaw-{m.group(1)}-{m.group(2)}", m.group(3)
    m = HREF_CFR.match(href)
    if m:
        key = m.group(2)
        return f"cfr-{m.group(1)}", key if key and "." in key else None
    if HREF_CONST.match(href):
        return "us-const", None
    return None


# ── plain-text citations (eCFR) ─────────────────────────────────────────────

_SEC = r"[0-9]+[a-zA-Z]*(?:[-–][0-9]+[a-zA-Z]*)?"
TEXT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("usc", re.compile(r"\b(\d{1,2})\s+U\.\s?S\.\s?C\.(?:A\.)?\s+(?:§+\s*)?(" + _SEC + r")")),
    ("usc-title", re.compile(r"\btitle\s+(\d{1,2}),?\s+United\s+States\s+Code\b", re.I)),
    ("cfr", re.compile(r"\b(\d{1,2})\s+CFR\s+(?:parts?\s+)?(\d+)(?:\.(" + _SEC + r"))?")),
    ("plaw", re.compile(r"\bPub(?:lic)?\.?\s*L(?:aw)?\.?\s*(?:No\.\s*)?(\d{2,3})[-–](\d{1,3})\b")),
    ("self", re.compile(r"§+\s*(\d+\.\d+[a-zA-Z]*(?:-\d+[a-zA-Z]*)?)")),
]


def text_citations(text: str, own_slug: str) -> list[dict]:
    """Find citations in plain text. Overlapping matches keep the earliest, longest one."""
    found: list[tuple[int, int, str, tuple[str, str | None]]] = []
    for kind, rx in TEXT_PATTERNS:
        for m in rx.finditer(text):
            if kind == "usc":
                tgt = (f"usc-{int(m.group(1))}", m.group(2).replace("–", "-"))
            elif kind == "usc-title":
                tgt = (f"usc-{int(m.group(1))}", None)
            elif kind == "cfr":
                key = f"{m.group(2)}.{m.group(3)}" if m.group(3) else None
                tgt = (f"cfr-{int(m.group(1))}", key)
            elif kind == "plaw":
                tgt = (f"plaw-{int(m.group(1))}-{int(m.group(2))}", None)
            else:
                tgt = (own_slug, m.group(1))
            found.append((m.start(), m.end(), text[m.start():m.end()], tgt))
    found.sort(key=lambda f: (f[0], -(f[1] - f[0])))
    out: list[dict] = []
    last_end = -1
    for start, end, quote, tgt in found:
        if start < last_end:
            continue
        out.append({"quote": quote, "target": tgt})
        last_end = end
    return out


def classify(own_slug: str, target: tuple[str, str | None] | None,
             law_ids: dict[str, int], keys: dict[int, set[str]]) -> dict:
    """Resolve one reference against the laws present in the database."""
    if target is None:
        return {"ref_kind": "unresolved", "target_law_id": None,
                "target_article_key": None, "resolved": 0}
    slug, key = target
    law_id = law_ids.get(slug)
    if law_id is None:
        return {"ref_kind": "unresolved", "target_law_id": None,
                "target_article_key": None, "resolved": 0}
    kind = "self-article" if slug == own_slug else ("constitution" if slug == "us-const" else "law")
    if kind == "self-article" and key is None:
        kind = "law"
    resolved = 1 if key is None or key in keys.get(law_id, ()) else 0
    return {"ref_kind": kind, "target_law_id": law_id, "target_article_key": key,
            "resolved": resolved}
