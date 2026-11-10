# Changelog

All notable changes to this repository. Versions follow semantic versioning: the major
version changes when the schema changes in a way that breaks queries.

## 1.0.0 — 2026-11-10

First release.

- Corpora: the Constitution (GPO CONAN 2022 literal print); the United States Code at OLRC
  release point 119-138 (current through Public Law 119-138, 2026-11-03), 53 titles and 4
  appendices in USLM XML; the eCFR, 49 titles up to date as of 2026-11-06; the Public Laws
  of the 118th Congress (274) and the 119th Congress through Public Law 119-138 (138).
- One SQLite schema shared with `leyes-federales-mx`: laws, law_versions, divisions,
  articles, article_parts, annotations, cross_references, FTS5 search and three views.
- Fetchers that pin every source file by URL and sha256 in `manifests/`, and a build that
  refuses a file whose hash does not match.
- Release assets: the combined database, one database and one JSONL export per corpus, the
  citation graph as CSV, the catalog of laws, and `SHA256SUMS`.
