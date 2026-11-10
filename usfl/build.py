"""Build the SQLite database from the fetched sources.

    python3 -m usfl build                       # all four corpora -> dist/us-federal-law.sqlite
    python3 -m usfl build --corpus constitution # no download needed (committed data)
    python3 -m usfl build --corpus usc --only 9,28a --db dist/usc-sample.sqlite

Every source file is checked against the sha256 in its manifest before it is mapped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
import zipfile
from pathlib import Path

from . import __version__
from .citations import classify, target_from_href, text_citations
from .common import (
    CORPORA, DATA_DIR, DIST_DIR, MANIFEST_DIR, SCHEMA_PATH, SOURCE_DIR, log, read_manifest,
    sha256_file, sha256_text,
)
from .constitution import parse as parse_constitution, pdf_text
from .ecfr import map_ecfr
from .uslm import disambiguate_keys, map_uslm

CONVERTER = f"us-federal-law {__version__}"
CORPUS_ROWS = {
    "constitution": ("us-constitution", "Constitution of the United States",
                     "https://www.govinfo.gov/app/collection/conan"),
    "usc": ("us-code", "United States Code (Office of the Law Revision Counsel)",
            "https://uscode.house.gov/"),
    "ecfr": ("ecfr", "Electronic Code of Federal Regulations",
             "https://www.ecfr.gov/"),
    "plaw": ("public-laws", "Public Laws of the United States (GovInfo)",
             "https://www.govinfo.gov/app/collection/plaw"),
}
SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or",
         "the", "to", "with", "under"}


class SourceMismatch(RuntimeError):
    pass


def smart_title(s: str) -> str:
    """'THE PUBLIC HEALTH AND WELFARE' -> 'The Public Health and Welfare'."""
    if not s or not s.isupper():
        return s
    words = s.lower().split(" ")
    out = []
    for i, w in enumerate(words):
        core = w.strip("(“\"'")
        if i and core in SMALL:
            out.append(w)
        else:
            j = len(w) - len(w.lstrip("(“\"'"))
            out.append(w[:j] + w[j:j + 1].upper() + w[j + 1:])
    return " ".join(out)


def check_source(path: Path, want: str | None) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing; run: python3 -m usfl fetch")
    if want and sha256_file(path) != want:
        raise SourceMismatch(f"{path}: sha256 differs from the manifest; re-run fetch")


class Builder:
    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.refs: list[tuple] = []           # (lv_id, article_id, own_slug, quote, target)
        self.corpus_ids: dict[str, int] = {}

    # ── rows ──
    def corpus(self, corpus: str) -> int:
        if corpus in self.corpus_ids:
            return self.corpus_ids[corpus]
        slug, name, url = CORPUS_ROWS[corpus]
        self.con.execute("INSERT OR IGNORE INTO jurisdictions(code, name) VALUES ('US', 'United States of America')")
        cur = self.con.execute("INSERT INTO corpora(jurisdiction, slug, name, source_url) VALUES ('US', ?, ?, ?)",
                               (slug, name, url))
        self.corpus_ids[corpus] = cur.lastrowid
        return cur.lastrowid

    def load(self, corpus: str, law: dict, version: dict, mapped: dict,
             text_refs: bool = False) -> dict:
        con = self.con
        arts = mapped["articles"]
        disambiguate_keys(arts)
        cur = con.execute(
            """INSERT INTO laws(corpus_id, slug, numero, nombre, nombre_corto, codigo_archivo, tipo,
                 estado_vigencia, dof_original, url_detalle)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (self.corpus(corpus), law["slug"], law.get("numero"), law["nombre"],
             law.get("nombre_corto"), law.get("codigo_archivo"), law["tipo"],
             law.get("estado_vigencia", "vigente"), law.get("dof_original"),
             law.get("url_detalle")))
        law_id = cur.lastrowid
        h = hashlib.sha256()
        for a in arts:
            h.update(a["text"].encode("utf-8"))
            h.update(b"\n")
        cur = con.execute(
            """INSERT INTO law_versions(law_id, captured_at, ultima_reforma, reforma_date,
                 ultima_reforma_indice, source_kind, resolved_url, source_sha256, source_bytes,
                 converter, text_sha256)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (law_id, version["captured_at"], version.get("ultima_reforma"),
             version.get("reforma_date"), version.get("ultima_reforma_indice"),
             version["source_kind"], version.get("resolved_url"),
             version["source_sha256"], version.get("source_bytes"), CONVERTER, h.hexdigest()))
        lv = cur.lastrowid
        div_ids: dict[int, int] = {}
        for d in mapped["divisions"]:
            cur = con.execute(
                """INSERT INTO divisions(law_version_id, parent_id, kind, ordinal_label, heading, rubric,
                     depth, seq, path) VALUES (?,?,?,?,?,?,?,?,?)""",
                (lv, div_ids.get(d["parent_seq"]) if d.get("parent_seq") else None, d["kind"],
                 d.get("ordinal_label"), d["heading"] or d["kind"], d.get("rubric"), d["depth"],
                 d["seq"], d.get("path")))
            div_ids[d["seq"]] = cur.lastrowid
        art_ids: dict[int, int] = {}
        n_parts = 0
        for a in arts:
            cur = con.execute(
                """INSERT INTO articles(law_version_id, law_id, division_id, article_key, numero_int,
                     suffix, label, seq, is_transitorio, decree_group, text, text_sha256, char_count,
                     last_reform_date) VALUES (?,?,?,?,?,?,?,?,0,NULL,?,?,?,?)""",
                (lv, law_id, div_ids.get(a["division_seq"]) if a.get("division_seq") else None,
                 a["article_key"], a.get("numero_int"), a.get("suffix"), a["label"], a["seq"],
                 a["text"], a["text_sha256"], a["char_count"], a.get("last_reform_date")))
            aid = cur.lastrowid
            art_ids[a["seq"]] = aid
            if a.get("parts"):
                con.executemany(
                    "INSERT INTO article_parts(article_id, kind, label, seq, text, text_sha256) VALUES (?,?,?,?,?,?)",
                    [(aid, p["kind"], p.get("label"), p["seq"], p["text"], p["text_sha256"])
                     for p in a["parts"]])
                n_parts += len(a["parts"])
            for r in a.get("refs") or []:
                self.refs.append((lv, aid, law["slug"], r["quote"] or r["href"],
                                  target_from_href(r["href"])))
            if text_refs:
                for r in text_citations(a["text"], law["slug"]):
                    self.refs.append((lv, aid, law["slug"], r["quote"], r["target"]))
        rows = []
        for i, n in enumerate(mapped.get("annotations") or [], 1):
            rows.append((lv, art_ids.get(n.get("article_seq")) if n.get("article_seq") else None,
                         div_ids.get(n.get("division_seq")) if n.get("division_seq") else None,
                         i, n.get("target_kind"), n.get("action"),
                         json.dumps(n["dates"]) if n.get("dates") else None, n["text"]))
        con.executemany(
            """INSERT INTO annotations(law_version_id, article_id, division_id, seq, target_kind, action,
                 dof_dates, text) VALUES (?,?,?,?,?,?,?,?)""", rows)
        return {"slug": law["slug"], "articles": len(arts), "divisions": len(mapped["divisions"]),
                "parts": n_parts, "annotations": len(rows)}

    def resolve_refs(self) -> dict:
        con = self.con
        law_ids = {s: i for s, i in con.execute("SELECT slug, id FROM laws")}
        keys: dict[int, set[str]] = {}
        for lid, k in con.execute("SELECT law_id, article_key FROM articles"):
            keys.setdefault(lid, set()).add(k)
        seqs: dict[int, int] = {}
        rows = []
        for lv, aid, own, quote, tgt in self.refs:
            c = classify(own, tgt, law_ids, keys)
            seqs[aid] = seqs.get(aid, 0) + 1
            rows.append((lv, aid, seqs[aid], quote, c["ref_kind"], c["target_law_id"],
                         c["target_article_key"], c["resolved"]))
        con.executemany(
            """INSERT INTO cross_references(law_version_id, article_id, seq, quote, ref_kind,
                 target_law_id, target_article_key, resolved) VALUES (?,?,?,?,?,?,?,?)""", rows)
        self.refs = []
        return {"cross_references": len(rows)}


# ── corpora ─────────────────────────────────────────────────────────────────

def build_constitution(b: Builder, source_dir: Path, manifest_dir: Path, data_dir: Path) -> list[dict]:
    man = read_manifest("constitution", manifest_dir)
    if not man:
        raise FileNotFoundError("manifests/constitution.json is missing")
    files = {f["key"]: f for f in man["files"]}
    paths = {k: source_dir / f["path"] for k, f in files.items()}
    if all(p.exists() for p in paths.values()):
        for k, p in paths.items():
            check_source(p, files[k].get("sha256"))
        mapped = parse_constitution(pdf_text(paths["constitution"]), pdf_text(paths["amendments"]),
                                    pdf_text(paths["constitution"], layout=False))
        how = "mapped from the PDFs"
    else:
        mapped = json.loads((data_dir / "constitution" / "constitution.json").read_text(encoding="utf-8"))
        how = "loaded from data/constitution/constitution.json"
    combined = sha256_text("\n".join(f["sha256"] for f in man["files"]))
    law = {"slug": "us-const", "numero": None,
           "nombre": "The Constitution of the United States of America",
           "nombre_corto": "U.S. Const.", "codigo_archivo": man["edition"]["package_id"],
           "tipo": "constitucion", "url_detalle": man.get("index_url")}
    version = {"captured_at": man.get("fetched_at"), "ultima_reforma": "Amendment XXVII (1992)",
               "reforma_date": "1992-05-07", "source_kind": man["source_kind"],
               "resolved_url": man.get("index_url"), "source_sha256": combined,
               "source_bytes": sum(f.get("bytes") or 0 for f in man["files"])}
    rec = b.load("constitution", law, version, mapped)
    rec["how"] = how
    log(f"  us-const: {rec['articles']} articles ({how})")
    return [rec]


def _usc_sort(key: str) -> tuple[int, str]:
    m = re.match(r"(\d+)(a?)", key)
    return (int(m.group(1)), m.group(2)) if m else (999, key)


def build_usc(b: Builder, source_dir: Path, manifest_dir: Path, only: set[str] | None) -> list[dict]:
    man = read_manifest("usc", manifest_dir)
    if not man:
        raise FileNotFoundError("manifests/usc.json is missing; run: python3 -m usfl fetch usc")
    ed = man["edition"]
    out = []
    names: dict[str, str] = {}
    files = sorted(man["files"], key=lambda f: _usc_sort(f["key"]))
    for f in files:
        key = f["key"]
        if only and key not in only:
            continue
        path = source_dir / f["path"]
        check_source(path, f.get("sha256"))
        with zipfile.ZipFile(path) as z:
            member = f.get("member") or next(n for n in z.namelist() if n.endswith(".xml"))
            with z.open(member) as fh:
                mapped = map_uslm(fh)
        if not mapped["articles"]:
            log(f"  usc-{key}: no sections (eliminated title or appendix); skipped")
            out.append({"slug": f"usc-{key}", "skipped": "no sections"})
            continue
        base = key.rstrip("a")
        appendix = key.endswith("a")
        meta = mapped["meta"]
        if not appendix:
            names[base] = smart_title(meta.get("title_heading") or "")
            nombre = f"Title {base} — {names[base]}" if names[base] else f"Title {base}"
        else:
            parent = names.get(base)
            nombre = f"Title {base}, Appendix" + (f" — {parent}" if parent else "")
        cite = f"{base} U.S.C." + (" App." if appendix else "")
        law = {"slug": f"usc-{key}", "numero": key, "nombre": nombre, "nombre_corto": cite,
               "codigo_archivo": Path(member).stem, "tipo": "codigo", "url_detalle": f["url"]}
        printed = str(meta.get("docPublicationName") or "").replace("Online@", "Public Law ")
        version = {"captured_at": man.get("fetched_at"),
                   "ultima_reforma": printed or ed.get("current_through"),
                   "reforma_date": ed.get("date"),
                   "ultima_reforma_indice": f"{ed.get('current_through')} ({ed.get('date')})",
                   "source_kind": man["source_kind"], "resolved_url": f["url"],
                   "source_sha256": f["sha256"], "source_bytes": f.get("bytes")}
        rec = b.load("usc", law, version, mapped)
        log(f"  usc-{key}: {rec['articles']} sections, {rec['divisions']} divisions")
        out.append(rec)
    return out


def build_ecfr(b: Builder, source_dir: Path, manifest_dir: Path, only: set[str] | None) -> list[dict]:
    man = read_manifest("ecfr", manifest_dir)
    if not man:
        raise FileNotFoundError("manifests/ecfr.json is missing; run: python3 -m usfl fetch ecfr")
    out = []
    for f in sorted(man["files"], key=lambda f: int(f["key"])):
        key = f["key"]
        if only and key not in only:
            continue
        path = source_dir / f["path"]
        check_source(path, f.get("sha256"))
        mapped = map_ecfr(path)
        cite = f"{key} CFR"
        law = {"slug": f"cfr-{key}", "numero": key,
               "nombre": f"Title {key} — {f.get('name') or ''}".rstrip(" —"),
               "nombre_corto": cite, "codigo_archivo": f"title-{key}", "tipo": "reglamento",
               "url_detalle": f"https://www.ecfr.gov/current/title-{key}"}
        amended = f.get("latest_amended_on")
        version = {"captured_at": man.get("fetched_at"),
                   "ultima_reforma": f"last amended {amended}" if amended else None,
                   "reforma_date": amended,
                   "ultima_reforma_indice": f"eCFR up to date as of {f.get('up_to_date_as_of')}",
                   "source_kind": man["source_kind"],
                   "resolved_url": f["url"], "source_sha256": f["sha256"],
                   "source_bytes": f.get("bytes")}
        rec = b.load("ecfr", law, version, mapped, text_refs=True)
        log(f"  cfr-{key}: {rec['articles']} sections, {rec['divisions']} divisions")
        out.append(rec)
    return out


def build_plaw(b: Builder, source_dir: Path, manifest_dir: Path, only: set[str] | None) -> list[dict]:
    man = read_manifest("plaw", manifest_dir)
    if not man:
        raise FileNotFoundError("manifests/plaw.json is missing; run: python3 -m usfl fetch plaw")
    out = []
    for f in sorted(man["files"], key=lambda f: f.get("sort", f["key"])):
        key = f["key"]
        if only and key not in only:
            continue
        path = source_dir / f["path"]
        check_source(path, f.get("sha256"))
        mapped = map_uslm(path)
        meta = mapped["meta"]
        congress, number = key.split("-")
        title = meta.get("short_title") or meta.get("official_title") or re.sub(
            r"^Public Law [\d–-]+:\s*", "", str(meta.get("title") or ""))
        cite = f"Pub. L. {congress}-{number}"
        pid = Path(f["path"]).stem
        law = {"slug": f"plaw-{key}", "numero": key, "nombre": title or cite, "nombre_corto": cite,
               "codigo_archivo": pid, "tipo": "ley", "dof_original": meta.get("approvedDate"),
               "url_detalle": f"https://www.govinfo.gov/app/details/{pid}"}
        version = {"captured_at": man.get("fetched_at"), "ultima_reforma": None,
                   "reforma_date": meta.get("approvedDate"), "source_kind": man["source_kind"],
                   "resolved_url": f["url"], "source_sha256": f["sha256"],
                   "source_bytes": f.get("bytes")}
        out.append(b.load("plaw", law, version, mapped))
    log(f"  plaw: {len(out)} Public Laws, {sum(r['articles'] for r in out)} sections")
    return out


def build(db: Path, corpora: list[str], *, source_dir: Path, manifest_dir: Path, data_dir: Path,
          only: set[str] | None = None, vacuum: bool = True) -> dict:
    db.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()
    con = sqlite3.connect(db)
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    con.execute("PRAGMA foreign_keys = OFF")     # checked by `usfl verify` afterwards
    con.execute("PRAGMA journal_mode = OFF")
    con.execute("PRAGMA synchronous = OFF")
    con.execute("PRAGMA cache_size = -262144")
    b = Builder(con)
    t0 = time.time()
    receipt: dict = {"db": str(db), "corpora": {}}
    for c in CORPORA:
        if c not in corpora:
            continue
        log(f"build {c}")
        if c == "constitution":
            recs = build_constitution(b, source_dir, manifest_dir, data_dir)
        elif c == "usc":
            recs = build_usc(b, source_dir, manifest_dir, only)
        elif c == "ecfr":
            recs = build_ecfr(b, source_dir, manifest_dir, only)
        else:
            recs = build_plaw(b, source_dir, manifest_dir, only)
        con.commit()
        loaded = [r for r in recs if "skipped" not in r]
        receipt["corpora"][c] = {
            "laws": len(loaded),
            "skipped": [r["slug"] for r in recs if "skipped" in r],
            "articles": sum(r["articles"] for r in loaded),
            "divisions": sum(r["divisions"] for r in loaded),
            "parts": sum(r["parts"] for r in loaded),
            "annotations": sum(r["annotations"] for r in loaded),
        }
    log("resolve cross references")
    receipt.update(b.resolve_refs())
    con.commit()
    con.execute("INSERT INTO articles_fts(articles_fts) VALUES ('optimize')")
    con.execute("ANALYZE")
    con.commit()
    if vacuum:
        con.execute("VACUUM")
    con.close()
    receipt["secs"] = round(time.time() - t0, 1)
    receipt["bytes"] = db.stat().st_size
    return receipt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="usfl build", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default="all", help="all, or comma-separated: " + ",".join(CORPORA))
    ap.add_argument("--only", help="USC/eCFR title keys or Public Law keys (118-5), comma-separated")
    ap.add_argument("--db", type=Path, default=DIST_DIR / "us-federal-law.sqlite",
                    help="output database (default dist/us-federal-law.sqlite; env USFL_DIST_DIR)")
    ap.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    ap.add_argument("--manifest-dir", type=Path, default=MANIFEST_DIR)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--no-vacuum", action="store_true")
    a = ap.parse_args(argv)
    corpora = list(CORPORA) if a.corpus == "all" else [c.strip() for c in a.corpus.split(",")]
    bad = [c for c in corpora if c not in CORPORA]
    if bad:
        ap.error(f"unknown corpus: {bad}")
    only = {x.strip().lower() for x in a.only.split(",") if x.strip()} if a.only else None
    try:
        receipt = build(a.db, corpora, source_dir=a.source_dir, manifest_dir=a.manifest_dir,
                        data_dir=a.data_dir, only=only, vacuum=not a.no_vacuum)
    except (FileNotFoundError, SourceMismatch) as e:
        log(f"error: {e}")
        return 1
    print(json.dumps(receipt, indent=2))
    return 0
