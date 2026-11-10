"""Integrity and count checks on a built database. Non-zero exit on any problem.

    python3 -m usfl verify --db dist/us-federal-law.sqlite

Checks: foreign keys and orphans; FTS index integrity; unique article keys per
edition; no empty article text; every law has at least one article; every edition's
source sha256 matches the committed manifest; law counts per corpus match the
manifests (minus documented skips); known citations resolve to the expected text.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from .common import DIST_DIR, MANIFEST_DIR, read_manifest, sha256_text

# Spot checks: (law slug, article key, words the text or label must contain).
SPOT_CHECKS = [
    ("us-const", "amd-1", "Congress shall make no law respecting an establishment of religion"),
    ("us-const", "I-8", "To regulate Commerce with foreign Nations"),
    ("usc-42", "1983", "Civil action for deprivation of rights"),
    ("usc-9", "2", "shall be valid, irrevocable, and enforceable"),
    ("usc-28a", "evid-rule-401", "Test for Relevant Evidence"),
    ("cfr-40", "141.86", "Monitoring requirements for lead and copper in tap water"),
    ("cfr-19", "102.20", "Specific rules by tariff classification"),
    ("plaw-118-42", "1", "Consolidated Appropriations Act, 2024"),
]
CORPUS_SLUG = {"constitution": "us-constitution", "usc": "us-code", "ecfr": "ecfr",
               "plaw": "public-laws"}
# Release-point files that carry no sections, by design of the publisher.
KNOWN_EMPTY = {"usc-50a"}


def run(db: Path, manifest_dir: Path, allow_partial: bool = False) -> dict:
    con = sqlite3.connect(db)
    one = lambda q, *a: con.execute(q, a).fetchone()[0]
    problems: list[str] = []
    notes: list[str] = []

    # 1 referential integrity
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    orphans = {
        "articles->law_versions": one("SELECT count(*) FROM articles a LEFT JOIN law_versions v ON v.id=a.law_version_id WHERE v.id IS NULL"),
        "articles->divisions": one("SELECT count(*) FROM articles a LEFT JOIN divisions d ON d.id=a.division_id WHERE a.division_id IS NOT NULL AND d.id IS NULL"),
        "divisions->parent": one("SELECT count(*) FROM divisions d LEFT JOIN divisions p ON p.id=d.parent_id WHERE d.parent_id IS NOT NULL AND p.id IS NULL"),
        "article_parts->articles": one("SELECT count(*) FROM article_parts p LEFT JOIN articles a ON a.id=p.article_id WHERE a.id IS NULL"),
        "annotations->law_versions": one("SELECT count(*) FROM annotations n LEFT JOIN law_versions v ON v.id=n.law_version_id WHERE v.id IS NULL"),
        "cross_references->target_laws": one("SELECT count(*) FROM cross_references x LEFT JOIN laws l ON l.id=x.target_law_id WHERE x.target_law_id IS NOT NULL AND l.id IS NULL"),
        "articles.law_id = law_versions.law_id": one("SELECT count(*) FROM articles a JOIN law_versions v ON v.id=a.law_version_id WHERE v.law_id<>a.law_id"),
    }
    if fk or any(orphans.values()):
        problems.append(f"referential integrity: fk_check={len(fk)} orphans={orphans}")

    # 2 full-text index
    try:
        con.execute("INSERT INTO articles_fts(articles_fts, rank) VALUES ('integrity-check', 1)")
    except sqlite3.DatabaseError as e:
        problems.append(f"FTS integrity-check failed: {e}")

    # 3 keys and text
    dups = one("SELECT count(*) FROM (SELECT law_version_id, article_key FROM articles GROUP BY 1,2 HAVING count(*)>1)")
    if dups:
        problems.append(f"duplicate article keys within an edition: {dups}")
    empty = one("SELECT count(*) FROM articles WHERE trim(text)=''")
    if empty:
        problems.append(f"articles with empty text: {empty}")
    bad_sha = one("SELECT count(*) FROM articles WHERE length(text_sha256)<>64")
    if bad_sha:
        problems.append(f"articles with malformed text_sha256: {bad_sha}")
    no_art = [r[0] for r in con.execute("SELECT slug FROM v_laws WHERE article_count=0")]
    if no_art:
        problems.append(f"laws without articles: {no_art[:10]}")
    placeholders = one("SELECT count(*) FROM articles WHERE text LIKE '[%]' AND text NOT LIKE '%\n%'")
    notes.append(f"placeholder articles (repealed/transferred/reserved/no text): {placeholders}")

    # 4 manifests: counts and source hashes
    counts = {}
    for corpus, cslug in CORPUS_SLUG.items():
        n = one("SELECT count(*) FROM laws l JOIN corpora c ON c.id=l.corpus_id WHERE c.slug=?", cslug)
        if n == 0:
            continue
        man = read_manifest(corpus, manifest_dir) or {"files": []}
        if corpus == "constitution":
            want = {"us-const": sha256_text("\n".join(f["sha256"] for f in man["files"]))}
        else:
            prefix = {"usc": "usc-", "ecfr": "cfr-", "plaw": "plaw-"}[corpus]
            want = {prefix + f["key"]: f.get("sha256") for f in man["files"]}
        got = dict(con.execute(
            "SELECT l.slug, v.source_sha256 FROM laws l JOIN law_versions v ON v.law_id=l.id "
            "JOIN corpora c ON c.id=l.corpus_id WHERE c.slug=?", (cslug,)).fetchall())
        mism = [s for s, h in got.items() if want.get(s) != h]
        if mism:
            problems.append(f"{corpus}: source sha256 differs from manifest for {mism[:10]}")
        missing = sorted(set(want) - set(got) - KNOWN_EMPTY)
        counts[corpus] = {"laws": n, "manifest_files": len(want), "not_built": len(missing),
                          "articles": one("SELECT count(*) FROM articles a JOIN laws l ON l.id=a.law_id JOIN corpora c ON c.id=l.corpus_id WHERE c.slug=?", cslug)}
        if missing:
            msg = f"{corpus}: {len(missing)} manifest files not in this database, e.g. {missing[:5]}"
            (notes if allow_partial else problems).append(msg)

    # 5 citations
    xr = {f"{k}/{r}": n for k, r, n in con.execute(
        "SELECT ref_kind, resolved, count(*) FROM cross_references GROUP BY 1,2")}
    notes.append(f"cross_references by kind/resolved: {xr}")

    # 6 spot checks (only for laws present)
    spots = []
    for slug, key, words in SPOT_CHECKS:
        row = con.execute("SELECT a.label || ' ' || a.text FROM articles a JOIN laws l ON l.id=a.law_id "
                          "WHERE l.slug=? AND a.article_key=?", (slug, key)).fetchone()
        present = one("SELECT count(*) FROM laws WHERE slug=?", slug)
        if not present:
            continue
        ok = bool(row and words.lower() in row[0].lower())
        spots.append({"law": slug, "key": key, "ok": ok})
        if not ok:
            problems.append(f"spot check failed: {slug} {key} should contain {words!r}")

    totals = {t: one(f"SELECT count(*) FROM {t}") for t in
              ("laws", "law_versions", "divisions", "articles", "article_parts", "annotations",
               "cross_references")}
    return {"ok": not problems, "db": str(db), "totals": totals, "corpora": counts,
            "spot_checks": spots, "problems": problems, "notes": notes}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="usfl verify", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DIST_DIR / "us-federal-law.sqlite")
    ap.add_argument("--manifest-dir", type=Path, default=MANIFEST_DIR)
    ap.add_argument("--allow-partial", action="store_true",
                    help="a build of some titles only (--only) is not an error")
    a = ap.parse_args(argv)
    if not a.db.exists():
        print(f"{a.db} not found; run: python3 -m usfl build")
        return 1
    receipt = run(a.db, a.manifest_dir, a.allow_partial)
    print(json.dumps(receipt, indent=2))
    return 0 if receipt["ok"] else 1
