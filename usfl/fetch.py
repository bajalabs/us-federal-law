"""Download the official sources and pin them in committed manifests.

Two modes per corpus:

* default: reproduce the edition pinned in ``manifests/<corpus>.json``. Each file is
  downloaded only if missing locally, and every file (local or new) must match the
  manifest's sha256; a mismatch is an error.
* ``--refresh``: discover the current edition from the publisher, download it, and
  rewrite the manifest with the new URLs, sizes and sha256 values.

Sources (base URLs overridable by environment variable):

* Constitution: GPO, Constitution of the United States, Analysis and Interpretation
  (CONAN), 2022 edition, literal-print granules, from GovInfo content URLs.
* United States Code: Office of the Law Revision Counsel (OLRC) release point, USLM
  XML, one zip per title or appendix.
* Code of Federal Regulations: eCFR versioner API, full XML per title as of the
  title's ``up_to_date_as_of`` date (reserved titles skipped).
* Public Laws: GovInfo bulk data, USLM XML per Public Law of the chosen Congresses.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import os

from .common import (
    CORPORA, MANIFEST_DIR, SOURCE_DIR, FetchError, http_download, http_get, log, now_iso,
    read_manifest, sha256_bytes, sha256_file, write_manifest,
)

GOVINFO_BASE = os.environ.get("USFL_GOVINFO_BASE", "https://www.govinfo.gov").rstrip("/")
OLRC_BASE = os.environ.get("USFL_OLRC_BASE", "https://uscode.house.gov").rstrip("/")
ECFR_BASE = os.environ.get("USFL_ECFR_BASE", "https://www.ecfr.gov").rstrip("/")
DEFAULT_CONGRESSES = os.environ.get("USFL_CONGRESSES", "118,119")

CONAN_PACKAGE = "GPO-CONAN-2022"
CONAN_GRANULES = (
    ("constitution", "GPO-CONAN-2022-4", "Constitution of the United States (literal print)"),
    ("amendments", "GPO-CONAN-2022-5", "Amendments to the Constitution (literal print)"),
)


# ── generic sync ────────────────────────────────────────────────────────────

def sync_files(files: list[dict], source_dir: Path, *, pinned: bool, workers: int = 1,
               sleep: float = 0.0) -> dict:
    """Ensure every manifest entry exists under source_dir with its sha256.

    pinned=True: an entry's sha256 is authoritative; mismatches raise.
    pinned=False: entries without sha256 (fresh discovery) get it recorded.
    """
    stats = {"ok": 0, "downloaded": 0, "mismatch": [], "failed": []}

    def one(entry: dict) -> None:
        dest = source_dir / entry["path"]
        want = entry.get("sha256") if pinned else None
        if dest.exists():
            got = sha256_file(dest)
            if want is None or got == want:
                entry["sha256"], entry["bytes"] = got, dest.stat().st_size
                stats["ok"] += 1
                return
        try:
            got, n = http_download(entry["url"], dest)
        except FetchError as e:
            stats["failed"].append({"path": entry["path"], "error": str(e)})
            log(f"  failed {entry['path']}: {e}")
            return
        if want is not None and got != want:
            stats["mismatch"].append({"path": entry["path"], "want": want, "got": got})
            log(f"  sha256 mismatch {entry['path']} (upstream changed; use --refresh to re-pin)")
            return
        entry["sha256"], entry["bytes"] = got, n
        stats["downloaded"] += 1
        log(f"  {entry['path']}  {n:,} bytes")
        if sleep:
            time.sleep(sleep)

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, files))
    else:
        for e in files:
            one(e)
    return stats


def _finish(corpus: str, manifest: dict, stats: dict, manifest_dir: Path, write: bool) -> int:
    if write:
        manifest["files"] = sorted(manifest["files"], key=lambda f: f.get("sort", f["path"]))
        p = write_manifest(corpus, manifest, manifest_dir)
        log(f"{corpus}: manifest written to {p}")
    n = len(manifest["files"])
    log(f"{corpus}: {n} files; {stats['ok']} verified locally, {stats['downloaded']} downloaded, "
        f"{len(stats['mismatch'])} mismatched, {len(stats['failed'])} failed")
    return 1 if stats["mismatch"] or stats["failed"] else 0


# ── Constitution ────────────────────────────────────────────────────────────

def discover_constitution() -> dict:
    files = []
    for name, granule, title in CONAN_GRANULES:
        files.append({
            "key": name,
            "title": title,
            "granule": granule,
            "url": f"{GOVINFO_BASE}/content/pkg/{CONAN_PACKAGE}/pdf/{granule}.pdf",
            "path": f"constitution/{granule}.pdf",
        })
    return {
        "corpus": "constitution",
        "publisher": "U.S. Government Publishing Office (GovInfo)",
        "source_kind": "gpo-conan-literal-print-pdf",
        "edition": {
            "package_id": CONAN_PACKAGE,
            "name": "Constitution of the United States of America: Analysis and Interpretation, "
                    "2022 edition, literal print of the Constitution and its amendments",
            "note": "Text in force: the Constitution of 1787 and the 27 ratified amendments "
                    "(the latest ratified in 1992).",
        },
        "index_url": f"{GOVINFO_BASE}/app/details/{CONAN_PACKAGE}",
        "files": files,
    }


# ── United States Code (OLRC) ───────────────────────────────────────────────

RP_LINK = re.compile(r'href="(download/releasepoints/us/pl/(\d+)/(\d+[a-z]?)/xml_usc([0-9]+a?)@[^"]+\.zip)"')
RP_DATE = re.compile(r"current through Public Law\s+(\d+)[-–](\d+[a-z]?)\s*\(([A-Z][a-z]+\.? \d{1,2}, \d{4})\)", re.I)


def _usc_title_sort(key: str) -> tuple[int, str]:
    m = re.match(r"(\d+)(a?)", key)
    return (int(m.group(1)), m.group(2)) if m else (999, key)


def discover_usc(release_point: str | None = None) -> dict:
    """Parse OLRC's download page for the current release point's XML zips."""
    index_url = f"{OLRC_BASE}/download.html"
    page = http_get(index_url, timeout=60).decode("utf-8", "replace")
    links = RP_LINK.findall(page)
    date_iso = None
    m = RP_DATE.search(html.unescape(re.sub(r"<[^>]+>", " ", page)))
    if m:
        try:
            date_iso = datetime.strptime(m.group(3).replace(".", ""), "%B %d, %Y").date().isoformat()
        except ValueError:
            date_iso = None
    if release_point:
        congress, law = release_point.split("-", 1)
        titles = sorted({t for _, _, _, t in links}, key=_usc_title_sort) or None
        if not titles:
            raise FetchError("could not list titles from the OLRC download page")
        links = [(f"download/releasepoints/us/pl/{congress}/{law}/xml_usc{t}@{congress}-{law}.zip",
                  congress, law, t) for t in titles]
        if m and f"{m.group(1)}-{m.group(2)}" != release_point:
            date_iso = None
    if not links:
        raise FetchError(f"no USLM release-point links found on {index_url}")
    congress, law = links[0][1], links[0][2]
    files = []
    for href, _c, _l, t in links:
        key = t.lstrip("0") or "0"
        files.append({
            "key": key,
            "url": f"{OLRC_BASE}/{href}",
            "path": f"usc/{congress}-{law}/{href.rsplit('/', 1)[-1]}",
            "sort": f"{_usc_title_sort(key)[0]:03d}{_usc_title_sort(key)[1]}",
        })
    return {
        "corpus": "usc",
        "publisher": "Office of the Law Revision Counsel, U.S. House of Representatives",
        "source_kind": "olrc-uslm-xml",
        "edition": {
            "release_point": f"{congress}-{law}",
            "current_through": f"Public Law {congress}-{law}",
            "date": date_iso,
        },
        "index_url": index_url,
        "note": "One USLM XML zip per title or appendix. Title 53 is reserved and has no file.",
        "files": files,
    }


def usc_members(manifest: dict, source_dir: Path) -> None:
    """Record the XML member name and its sha256 inside each zip."""
    for f in manifest["files"]:
        p = source_dir / f["path"]
        if not p.exists():
            continue
        with zipfile.ZipFile(p) as z:
            xmls = [n for n in z.namelist() if n.lower().endswith(".xml")]
            if len(xmls) != 1:
                raise FetchError(f"{p.name}: expected one XML member, found {xmls}")
            data = z.read(xmls[0])
        f["member"], f["member_sha256"], f["member_bytes"] = xmls[0], sha256_bytes(data), len(data)


# ── eCFR ────────────────────────────────────────────────────────────────────

def discover_ecfr(only: set[str] | None = None) -> dict:
    index_url = f"{ECFR_BASE}/api/versioner/v1/titles.json"
    idx = json.loads(http_get(index_url, timeout=60, accept="application/json"))
    files, skipped = [], []
    for t in idx.get("titles") or []:
        n = int(t["number"])
        if only and str(n) not in only:
            continue
        if t.get("reserved"):
            skipped.append({"number": n, "name": t.get("name"), "reason": "reserved"})
            continue
        date = t.get("up_to_date_as_of")
        files.append({
            "key": str(n),
            "name": t.get("name"),
            "up_to_date_as_of": date,
            "latest_amended_on": t.get("latest_amended_on"),
            "latest_issue_date": t.get("latest_issue_date"),
            "url": f"{ECFR_BASE}/api/versioner/v1/full/{date}/title-{n}.xml",
            "path": f"ecfr/{date}/title-{n}.xml",
            "sort": f"{n:03d}",
        })
    return {
        "corpus": "ecfr",
        "publisher": "Office of the Federal Register and GPO, Electronic Code of Federal Regulations",
        "source_kind": "ecfr-versioner-full-xml",
        "edition": {"date": (idx.get("meta") or {}).get("date")},
        "index_url": index_url,
        "skipped": skipped,
        "note": "eCFR is an editorially compiled, continuously updated CFR; it is authoritative "
                "but not an official legal edition of the CFR.",
        "files": files,
    }


# ── Public Laws (GovInfo bulk data) ─────────────────────────────────────────

PLAW_NAME = re.compile(r"^PLAW-(\d+)publ(\d+)\.xml$")


def discover_plaw(congresses: list[int]) -> dict:
    files = []
    for c in congresses:
        url = f"{GOVINFO_BASE}/bulkdata/json/PLAW/{c}/public"
        listing = json.loads(http_get(url, timeout=60, accept="application/json"))
        for f in listing.get("files") or []:
            m = PLAW_NAME.match(f.get("name") or "")
            if not m or f.get("folder"):
                continue
            files.append({
                "key": f"{m.group(1)}-{m.group(2)}",
                "url": f.get("link") or f"{GOVINFO_BASE}/bulkdata/PLAW/{c}/public/{f['name']}",
                "path": f"plaw/{c}/{f['name']}",
                "sort": f"{int(m.group(1)):04d}-{int(m.group(2)):05d}",
            })
    return {
        "corpus": "plaw",
        "publisher": "U.S. Government Publishing Office (GovInfo bulk data), Public and Private Laws",
        "source_kind": "govinfo-plaw-uslm-xml",
        "edition": {"congresses": congresses, "date": now_iso()[:10]},
        "index_url": f"{GOVINFO_BASE}/bulkdata/PLAW",
        "files": files,
    }


# ── driver ──────────────────────────────────────────────────────────────────

def run(corpus: str, *, source_dir: Path, manifest_dir: Path, refresh: bool,
        release_point: str | None, congresses: list[int], only: set[str] | None,
        workers: int) -> int:
    pinned = read_manifest(corpus, manifest_dir)
    if refresh or pinned is None:
        if corpus == "constitution":
            fresh = discover_constitution()
        elif corpus == "usc":
            fresh = discover_usc(release_point)
        elif corpus == "ecfr":
            fresh = discover_ecfr(only)
        else:
            fresh = discover_plaw(congresses)
        fresh["fetched_at"] = now_iso()
        if only and corpus in ("usc", "ecfr"):
            fresh["files"] = [f for f in fresh["files"] if f["key"] in only]
        stats = sync_files(fresh["files"], source_dir, pinned=False,
                           workers=workers if corpus == "plaw" else 1)
        if corpus == "usc":
            usc_members(fresh, source_dir)
        if only and pinned:
            # a partial refresh replaces only the named titles in the pinned manifest
            keep = [f for f in pinned["files"] if f["key"] not in only]
            fresh["files"] = keep + fresh["files"]
        return _finish(corpus, fresh, stats, manifest_dir, write=True)
    files = pinned["files"]
    if only and corpus in ("usc", "ecfr"):
        files = [f for f in files if f["key"] in only]
    stats = sync_files(files, source_dir, pinned=True, workers=workers if corpus == "plaw" else 1)
    return _finish(corpus, {**pinned, "files": files}, stats, manifest_dir, write=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="usfl fetch", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus", choices=[*CORPORA, "all"])
    ap.add_argument("--source-dir", type=Path, default=SOURCE_DIR,
                    help="where downloads go (env USFL_SOURCE_DIR, default ./source)")
    ap.add_argument("--manifest-dir", type=Path, default=MANIFEST_DIR,
                    help="where manifests live (env USFL_MANIFEST_DIR, default ./manifests)")
    ap.add_argument("--refresh", action="store_true",
                    help="discover the current edition and re-pin the manifest")
    ap.add_argument("--release-point", help="USC only, with --refresh: e.g. 119-138")
    ap.add_argument("--congress", default=DEFAULT_CONGRESSES,
                    help="Public Laws only: comma-separated Congresses (default 118,119)")
    ap.add_argument("--only", help="USC/eCFR: comma-separated title keys, e.g. 9,28a")
    ap.add_argument("--workers", type=int, default=4, help="parallel downloads for Public Laws")
    a = ap.parse_args(argv)
    only = {x.strip().lower() for x in a.only.split(",") if x.strip()} if a.only else None
    congresses = [int(x) for x in a.congress.split(",") if x.strip()]
    corpora = CORPORA if a.corpus == "all" else (a.corpus,)
    rc = 0
    for c in corpora:
        log(f"fetch {c}{' (refresh)' if a.refresh else ''}")
        rc |= run(c, source_dir=a.source_dir, manifest_dir=a.manifest_dir, refresh=a.refresh,
                  release_point=a.release_point, congresses=congresses, only=only,
                  workers=a.workers)
    return rc
