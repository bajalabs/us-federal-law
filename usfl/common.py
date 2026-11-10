"""Shared helpers: paths, HTTP with TLS validation, hashing, manifests, text cleanup.

Standard library only. Every network default can be changed with an argument or an
environment variable; nothing here is specific to one machine.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent

# Defaults, each overridable by environment variable (and by CLI flags where it matters).
SOURCE_DIR = Path(os.environ.get("USFL_SOURCE_DIR", "source"))
DIST_DIR = Path(os.environ.get("USFL_DIST_DIR", "dist"))
MANIFEST_DIR = Path(os.environ.get("USFL_MANIFEST_DIR", str(REPO_ROOT / "manifests")))
DATA_DIR = Path(os.environ.get("USFL_DATA_DIR", str(REPO_ROOT / "data")))
SCHEMA_PATH = REPO_ROOT / "schema" / "schema.sql"
USER_AGENT = os.environ.get(
    "USFL_USER_AGENT",
    f"us-federal-law/{__version__} (+https://github.com/bajalabs/us-federal-law)",
)
# The GovInfo API (api.govinfo.gov) needs a key; DEMO_KEY works for a handful of calls.
# Bulk-data and content URLs on www.govinfo.gov need none. The key is sent as a header,
# never in a URL, and never written to a manifest or a log.
GOVINFO_API_KEY_ENV = "GOVINFO_API_KEY"
GOVINFO_API_HOST = "api.govinfo.gov"

CORPORA = ("constitution", "usc", "ecfr", "plaw")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ── text ────────────────────────────────────────────────────────────────────

_WS = re.compile(r"\s+")


def collapse(s: str | None) -> str:
    return _WS.sub(" ", s or "").strip()


def local(tag: Any) -> str:
    """Local name of an ElementTree tag ('{ns}section' -> 'section')."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def slugish(s: str | None, n: int = 60) -> str:
    out = re.sub(r"[^a-zA-Z0-9.]+", "-", (s or "").strip()).strip("-").lower()
    return (out or "x")[:n]


def numero_int(key: str | None) -> int | None:
    m = re.match(r"(\d+)", key or "")
    return int(m.group(1)) if m else None


def key_suffix(key: str | None) -> str | None:
    """'78u-4' -> 'u-4'; '1983' -> None. Mirrors the sister schema's `suffix` column."""
    m = re.match(r"\d+(.+)$", key or "")
    return m.group(1) if m else None


# ── HTTP ────────────────────────────────────────────────────────────────────

class FetchError(RuntimeError):
    pass


def _headers(url: str, accept: str | None) -> dict[str, str]:
    # eCFR's full-XML endpoint refuses uncompressed responses (HTTP 406), so ask
    # for gzip everywhere and decode it here; hashes are of the decoded content.
    h = {"User-Agent": USER_AGENT, "Accept": accept or "*/*", "Accept-Encoding": "gzip"}
    host = urllib.parse.urlsplit(url).hostname or ""
    if host == GOVINFO_API_HOST:
        h["X-Api-Key"] = os.environ.get(GOVINFO_API_KEY_ENV) or "DEMO_KEY"
    return h


def _redact(s: str) -> str:
    key = os.environ.get(GOVINFO_API_KEY_ENV) or ""
    return s.replace(key, "***") if key else s


def _open(url: str, timeout: int, accept: str | None):
    ctx = ssl.create_default_context()  # certificate and hostname validation stay on
    req = urllib.request.Request(url, headers=_headers(url, accept))
    return urllib.request.urlopen(req, context=ctx, timeout=timeout)


def http_get(url: str, *, timeout: int = 120, retries: int = 5, accept: str | None = None) -> bytes:
    """GET into memory with backoff on 429/5xx and network errors."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with _open(url, timeout, accept) as resp:
                data = resp.read()
                if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                raise FetchError(f"HTTP {e.code} for {url}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
        if attempt < retries - 1:
            time.sleep(min(60.0, 2.0 * (2 ** attempt)))
    raise FetchError(_redact(f"GET failed after {retries} attempts: {url}: {last}"))


def http_download(url: str, dest: Path, *, timeout: int = 300, retries: int = 5) -> tuple[str, int]:
    """Stream a URL to `dest` (atomic rename), returning (sha256, bytes)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    last: Exception | None = None
    for attempt in range(retries):
        try:
            h = hashlib.sha256()
            n = 0
            with _open(url, timeout, None) as resp, open(tmp, "wb") as out:
                gz = (resp.headers.get("Content-Encoding") or "").lower() == "gzip"
                dec = zlib.decompressobj(16 + zlib.MAX_WBITS) if gz else None
                while True:
                    block = resp.read(1 << 20)
                    if not block:
                        break
                    if dec is not None:
                        block = dec.decompress(block)
                    h.update(block)
                    out.write(block)
                    n += len(block)
                if dec is not None:
                    block = dec.flush()
                    h.update(block)
                    out.write(block)
                    n += len(block)
            tmp.replace(dest)
            return h.hexdigest(), n
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                raise FetchError(f"HTTP {e.code} for {url}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
        if attempt < retries - 1:
            time.sleep(min(60.0, 2.0 * (2 ** attempt)))
    try:
        tmp.unlink()
    except OSError:
        pass
    raise FetchError(_redact(f"download failed after {retries} attempts: {url}: {last}"))


# ── manifests ───────────────────────────────────────────────────────────────

def manifest_path(corpus: str, manifest_dir: Path | None = None) -> Path:
    return (manifest_dir or MANIFEST_DIR) / f"{corpus}.json"


def read_manifest(corpus: str, manifest_dir: Path | None = None) -> dict | None:
    p = manifest_path(corpus, manifest_dir)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def write_manifest(corpus: str, data: dict, manifest_dir: Path | None = None) -> Path:
    p = manifest_path(corpus, manifest_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p
