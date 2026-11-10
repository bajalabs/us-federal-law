"""Release assets from a built database.

    python3 -m usfl export --db dist/us-federal-law.sqlite

Writes to dist/release/ (env USFL_DIST_DIR):
  us-federal-law.sqlite.zst       the combined database (all corpora)
  usfl-<corpus>.sqlite.zst        one database per corpus: that corpus's text plus the
                                  full catalog of laws, so citations into other corpora
                                  keep their target ids
  usfl-<corpus>-articles.jsonl.zst one JSON object per article, with its hierarchy
  usfl-citations.csv.zst          the citation graph, one row per reference
  laws.csv                        the catalog: one row per law with its edition and hash
  SHA256SUMS                      sha256 of every file above

Compression uses the `zstd` command when present, else zip (stdlib). No asset may
exceed 2 GB; the export stops if one would.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import subprocess
import zipfile
from pathlib import Path

from .common import CORPORA, DIST_DIR, SCHEMA_PATH, log, sha256_file

CORPUS_SLUG = {"constitution": "us-constitution", "usc": "us-code", "ecfr": "ecfr",
               "plaw": "public-laws"}
MAX_ASSET = 2 * 1024 ** 3 - 1
SAMPLES = {  # small committed samples: (law slug, file name, max articles)
    "usc-9": ("usc-9.jsonl", None),
    "cfr-1": ("cfr-1.jsonl", 40),
    "plaw-118-5": ("plaw-118-5.jsonl", None),
}


def corpus_db(src: Path, dest: Path, corpus: str) -> None:
    if dest.exists():
        dest.unlink()
    con = sqlite3.connect(dest)
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    con.execute("PRAGMA journal_mode = OFF")
    con.execute("PRAGMA synchronous = OFF")
    con.execute("ATTACH DATABASE ? AS src", (str(src),))
    cslug = CORPUS_SLUG[corpus]
    law_ids = "(SELECT l.id FROM src.laws l JOIN src.corpora c ON c.id=l.corpus_id WHERE c.slug='%s')" % cslug
    lv_ids = f"(SELECT id FROM src.law_versions WHERE law_id IN {law_ids})"
    for sql in (
        "INSERT INTO jurisdictions SELECT * FROM src.jurisdictions",
        "INSERT INTO corpora SELECT * FROM src.corpora",
        "INSERT INTO laws SELECT * FROM src.laws",
        "INSERT INTO law_versions SELECT * FROM src.law_versions",
        f"INSERT INTO divisions SELECT * FROM src.divisions WHERE law_version_id IN {lv_ids}",
        f"INSERT INTO articles SELECT * FROM src.articles WHERE law_id IN {law_ids}",
        f"INSERT INTO article_parts SELECT p.* FROM src.article_parts p JOIN src.articles a ON a.id=p.article_id WHERE a.law_id IN {law_ids}",
        f"INSERT INTO annotations SELECT * FROM src.annotations WHERE law_version_id IN {lv_ids}",
        f"INSERT INTO cross_references SELECT * FROM src.cross_references WHERE law_version_id IN {lv_ids}",
    ):
        con.execute(sql)
    con.commit()
    con.execute("DETACH DATABASE src")
    con.execute("INSERT INTO articles_fts(articles_fts) VALUES ('optimize')")
    con.execute("ANALYZE")
    con.commit()
    con.execute("VACUUM")
    con.close()


def _hierarchy(con: sqlite3.Connection) -> dict[int, list[str]]:
    divs = {i: (p, h, r) for i, p, h, r in con.execute(
        "SELECT id, parent_id, heading, rubric FROM divisions")}
    memo: dict[int, list[str]] = {}

    def chain(i: int) -> list[str]:
        if i in memo:
            return memo[i]
        p, h, r = divs[i]
        me = f"{h} — {r}" if r else h
        memo[i] = (chain(p) if p in divs else []) + [me]
        return memo[i]

    return {i: chain(i) for i in divs}


def articles_jsonl(con: sqlite3.Connection, dest: Path, where: str, args: tuple = (),
                   limit: int | None = None) -> int:
    hier = _hierarchy(con)
    n = 0
    q = ("SELECT l.slug, l.nombre_corto, a.article_key, a.label, a.division_id, a.text, "
         "a.last_reform_date, a.char_count, a.text_sha256 FROM articles a "
         "JOIN laws l ON l.id = a.law_id JOIN corpora c ON c.id = l.corpus_id "
         f"WHERE {where} ORDER BY l.id, a.seq" + (f" LIMIT {int(limit)}" if limit else ""))
    with open(dest, "w", encoding="utf-8") as out:
        for slug, cite, key, label, div, text, last, chars, sha in con.execute(q, args):
            out.write(json.dumps({
                "law": slug, "cite": cite, "key": key, "label": label,
                "hierarchy": hier.get(div, []), "text": text, "last_reform_date": last,
                "char_count": chars, "text_sha256": sha}, ensure_ascii=False) + "\n")
            n += 1
    return n


def laws_csv(con: sqlite3.Connection, dest: Path) -> int:
    rows = con.execute(
        "SELECT slug, corpus_slug, numero, nombre, nombre_corto, tipo, dof_original, "
        "ultima_reforma, reforma_date, ultima_reforma_indice, source_kind, resolved_url, "
        "source_sha256, article_count, division_count FROM v_laws ORDER BY id").fetchall()
    with open(dest, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["slug", "corpus", "numero", "name", "cite", "tipo", "enacted",
                    "last_amendment", "last_amendment_date", "edition", "source_kind",
                    "source_url", "source_sha256", "articles", "divisions"])
        w.writerows(rows)
    return len(rows)


def citations_csv(con: sqlite3.Connection, dest: Path) -> int:
    q = ("SELECT fl.slug, a.article_key, x.seq, x.quote, x.ref_kind, tl.slug, "
         "x.target_article_key, x.resolved FROM cross_references x "
         "JOIN articles a ON a.id = x.article_id JOIN laws fl ON fl.id = a.law_id "
         "LEFT JOIN laws tl ON tl.id = x.target_law_id ORDER BY x.id")
    n = 0
    with open(dest, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["from_law", "from_key", "seq", "quote", "ref_kind", "to_law", "to_key",
                    "resolved"])
        for row in con.execute(q):
            w.writerow(row)
            n += 1
    return n


def compress(path: Path, out_dir: Path, method: str, level: int) -> Path:
    if method == "zstd":
        dest = out_dir / (path.name + ".zst")
        subprocess.run(["zstd", "-q", "-f", "-T0", f"-{level}", "--long=27", str(path),
                        "-o", str(dest)], check=True)
    elif method == "zip":
        dest = out_dir / (path.name + ".zip")
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9,
                             allowZip64=True) as z:
            z.write(path, arcname=path.name)
    else:
        dest = out_dir / path.name
        shutil.copyfile(path, dest)
    if dest.stat().st_size > MAX_ASSET:
        raise RuntimeError(f"{dest.name} is {dest.stat().st_size:,} bytes, over the 2 GB asset limit")
    return dest


def write_samples(con: sqlite3.Connection, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    done = {}
    for slug, (name, limit) in SAMPLES.items():
        if con.execute("SELECT count(*) FROM laws WHERE slug=?", (slug,)).fetchone()[0]:
            done[name] = articles_jsonl(con, out / name, "l.slug = ?", (slug,), limit)
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="usfl export", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DIST_DIR / "us-federal-law.sqlite")
    ap.add_argument("--dist", type=Path, default=DIST_DIR)
    ap.add_argument("--compress", choices=["zstd", "zip", "none"],
                    default="zstd" if shutil.which("zstd") else "zip")
    ap.add_argument("--level", type=int, default=12, help="zstd level (default 12)")
    ap.add_argument("--samples", type=Path, help="also write small JSONL samples to this folder")
    a = ap.parse_args(argv)
    if not a.db.exists():
        log(f"{a.db} not found; run: python3 -m usfl build")
        return 1
    work, release = a.dist / "work", a.dist / "release"
    work.mkdir(parents=True, exist_ok=True)
    if release.exists():
        shutil.rmtree(release)
    release.mkdir(parents=True)
    con = sqlite3.connect(a.db)
    present = {c for c, in con.execute("SELECT slug FROM corpora")}
    assets: list[Path] = []
    log(f"compress {a.db.name}")
    assets.append(compress(a.db, release, a.compress, a.level))
    for corpus in CORPORA:
        if CORPUS_SLUG[corpus] not in present:
            continue
        db = work / f"usfl-{corpus}.sqlite"
        log(f"split {db.name}")
        corpus_db(a.db, db, corpus)
        assets.append(compress(db, release, a.compress, a.level))
        jl = work / f"usfl-{corpus}-articles.jsonl"
        n = articles_jsonl(con, jl, "c.slug = ?", (CORPUS_SLUG[corpus],))
        log(f"  {jl.name}: {n:,} articles")
        assets.append(compress(jl, release, a.compress, a.level))
    cit = work / "usfl-citations.csv"
    log(f"  {cit.name}: {citations_csv(con, cit):,} references")
    assets.append(compress(cit, release, a.compress, a.level))
    lc = release / "laws.csv"
    log(f"  laws.csv: {laws_csv(con, lc):,} laws")
    assets.append(lc)
    if a.samples:
        log(f"samples: {write_samples(con, a.samples)}")
    con.close()
    with open(release / "SHA256SUMS", "w", encoding="utf-8") as f:
        for p in sorted(assets, key=lambda p: p.name):
            f.write(f"{sha256_file(p)}  {p.name}\n")
    for p in sorted(assets, key=lambda p: p.name):
        print(f"{p.stat().st_size:>15,}  {p.name}")
    return 0
