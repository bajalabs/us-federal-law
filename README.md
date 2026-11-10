# us-federal-law

[![ci](https://github.com/bajalabs/us-federal-law/actions/workflows/ci.yml/badge.svg)](https://github.com/bajalabs/us-federal-law/actions/workflows/ci.yml)
[![code: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![data: CC0-1.0](https://img.shields.io/badge/data-CC0--1.0-lightgrey.svg)](LICENSE-DATA)
[![release](https://img.shields.io/github/v/release/bajalabs/us-federal-law)](https://github.com/bajalabs/us-federal-law/releases/latest)

The in-force US federal law stack — the Constitution, the United States Code, the
Electronic Code of Federal Regulations and the Public Laws of the 118th and 119th
Congresses — as one searchable SQLite database, for developers, researchers and anyone who
needs federal law as data rather than as web pages.

Every section is a row with its hierarchy, its structural parts (`(a)(1)(A)`), its editorial
notes and the citations it makes, built from the official sources with a sha256 manifest for
every file. The schema is the same as the sister repository
[`leyes-federales-mx`](https://github.com/bajalabs/leyes-federales-mx) (Mexican federal
laws), so one query works on both.

## Quick start

From a fresh clone, with Python 3.12 or later and nothing to install:

```sh
git clone https://github.com/bajalabs/us-federal-law.git
cd us-federal-law
python3 -m unittest                      # 38 tests on committed fixtures
python3 -m usfl build --corpus constitution --db dist/us-constitution.sqlite
python3 -m usfl verify --db dist/us-constitution.sqlite --allow-partial
python3 - <<'EOF'
import sqlite3
con = sqlite3.connect("dist/us-constitution.sqlite")
for label, text in con.execute(
        "SELECT a.label, a.text FROM articles_fts f JOIN articles a ON a.id = f.rowid "
        "WHERE articles_fts MATCH 'NEAR(speedy trial, 5)'"):
    print(label, "--", text[:120])
EOF
# Amendment VI -- In all criminal prosecutions, the accused shall enjoy the right to a speedy ...
```

The Constitution builds from its committed text (`data/constitution/`). For everything else,
download the release database (724 MB compressed, 2.7 GB unpacked):

```sh
curl -LO https://github.com/bajalabs/us-federal-law/releases/latest/download/us-federal-law.sqlite.zst
zstd -d us-federal-law.sqlite.zst
sqlite3 us-federal-law.sqlite "SELECT slug, nombre, article_count FROM v_laws LIMIT 5"
```

or build it yourself from the official sources (about 1 GB of downloads, then about three
minutes):

```sh
python3 -m usfl fetch all      # into ./source, every file checked against manifests/
python3 -m usfl build          # dist/us-federal-law.sqlite
python3 -m usfl verify         # integrity, counts, hashes, spot checks
```

## What is inside

| Corpus | Laws | Articles | Divisions | Parts | Annotations | Citations | Text (characters) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Constitution | 1 | 75 | 35 | 85 | 0 | 0 | 43,040 |
| United States Code | 57 (53 titles, 4 appendices) | 61,160 | 9,455 | 712,265 | 218,212 | 106,918 | 157.8 million |
| eCFR | 49 titles | 231,362 | 43,361 | 1,844,975 | 135,157 | 595,661 | 635.9 million |
| Public Laws | 412 | 9,137 | 1,098 | 59,885 | 0 | 15,067 | 16.4 million |
| **Total** | **519** | **301,734** | **53,949** | **2,617,210** | **353,369** | **717,646** | |

Articles include 18,721 placeholders for repealed, transferred, omitted and reserved sections.
Of the 717,646 citations, 661,809 (92 %) resolve to an article in this database; 27,195 name a
law that is here but a section that is not (a repealed section, a CFR section cited by a
prefix the CFR does not use); 28,642 point outside it (the Statutes at Large, earlier
Congresses' Public Laws).

| Corpus | Edition in this release | Source |
|---|---|---|
| Constitution | Literal print in GPO's *Constitution Annotated*, 2022 edition: the 1787 text and the 27 ratified amendments | [GovInfo GPO-CONAN-2022](https://www.govinfo.gov/app/details/GPO-CONAN-2022), granules 4 and 5 (PDF) |
| United States Code | OLRC release point **119-138**, current through Public Law 119-138 (2026-11-03); titles 1–54 (53 is reserved) and the appendices to titles 5, 11, 18 and 28 | [uscode.house.gov](https://uscode.house.gov/download.html), USLM XML |
| eCFR | All 49 titles in force (title 35 is reserved), up to date as of **2026-11-06** | [eCFR versioner API](https://www.ecfr.gov/developers/documentation/api/v1), full XML |
| Public Laws | 118th Congress (274 laws) and 119th Congress through Pub. L. 119-138 | [GovInfo bulk data](https://www.govinfo.gov/bulkdata/PLAW), USLM XML |

The Title 50 Appendix is in the release point but carries no sections (OLRC eliminated it and
moved its provisions to Title 50 and other titles), so it has no row.

## Schema

`schema/schema.sql` defines it; the tables and views are identical to `leyes-federales-mx`.

| Table | One row per | Notes |
|---|---|---|
| `jurisdictions` | jurisdiction | `US` here, `MX` in the sister repository |
| `corpora` | corpus | `us-constitution`, `us-code`, `ecfr`, `public-laws` |
| `laws` | instrument | the Constitution, a USC title or appendix, a CFR title, a Public Law |
| `law_versions` | captured edition | source URL, `source_sha256` (as in `manifests/`), edition date |
| `divisions` | heading above the sections | subtitle, chapter, subchapter, part, subpart, division, title, subject group; a tree by `parent_id` |
| `articles` | citable unit | a Constitution section or amendment, a USC section or court rule, a CFR section or appendix, a Public Law section or appropriation paragraph |
| `article_parts` | structural level inside an article | `label` is the full path, `(a)(1)(A)`; Constitution clauses are `cl. 1`, `cl. 2`; each part holds its own words, so the parts add up to the text |
| `annotations` | editorial note | USC source credits and notes (amendments, effective dates, short titles...), CFR citations, authority and source lines |
| `cross_references` | citation found in an article | verbatim `quote`, `target_law_id`, `target_article_key`, `resolved` |
| `articles_fts` | FTS5 index | over `label` and `text` of `articles` |
| `blocks`, `version_diffs`, `article_changes` | | shared with the sister schema; empty in this release (one edition per law) |
| `v_laws`, `v_articles`, `v_toc`, `v_reformas` | views | one-hop surfaces for queries and BI tools |

### Glossary of the shared column names

The column names are shared with the Mexican repository verbatim; in US terms they mean:

| Column | Meaning here |
|---|---|
| `laws.numero` | title or law number: `42`, `28a`, `40`, `118-42` |
| `laws.nombre`, `laws.nombre_corto` | full name; citation form: `42 U.S.C.`, `28 U.S.C. App.`, `40 CFR`, `Pub. L. 118-42`, `U.S. Const.` |
| `laws.tipo` | `constitucion` (Constitution), `codigo` (USC), `reglamento` (CFR), `ley` (Public Law) |
| `laws.estado_vigencia` | `vigente`, in force (see Limitations for Public Laws) |
| `laws.dof_original` | original enactment date; for a Public Law, its approval date |
| `law_versions.ultima_reforma`, `reforma_date` | latest amendment the edition reflects: `Public Law 119-138` (USC), `last amended 2026-10-30` (CFR) |
| `law_versions.ultima_reforma_indice` | what the publisher's index said: the release point, eCFR's "up to date as of" |
| `articles.last_reform_date` | latest date in the section's USC source credit or CFR Federal Register citation |
| `articles.is_transitorio`, `decree_group` | Mexican transitory articles; always 0 and NULL here |
| `annotations.action` | the USC note topic (`amendments`, `effectiveDate`, `shortTitle`, ...) |
| `annotations.dof_dates` | JSON array of the ISO dates an annotation mentions |

### Article keys

| Corpus | `article_key` | Examples |
|---|---|---|
| Constitution | article-section, amendment-section | `preamble`, `I-8`, `V`, `amd-1`, `amd-14-1`, `attestation` |
| USC | section number | `1983`, `78u-4`, `1395w-22`; court rules `civil-rule-12`, `evid-rule-401`; reorganization plans `reorgPlan/1970/3` |
| CFR | section number; appendices by name | `141.86`, `1.954-1`, `appendix-a-to-part-141` |
| Public Laws | section number | `101`; when an Act repeats a number across divisions, the USLM path `dA/tI/s101`; appropriation paragraphs `appropriation-12` |

Keys are unique within an edition. The few numbers the Code itself uses twice keep the plain
key on the first occurrence and `~2` on the second.

## Query cookbook

All queries run on the combined database (`us-federal-law.sqlite`) and on the per-corpus ones.

Full-text search, ranked, with a snippet:

```sql
SELECT l.nombre_corto, a.article_key, a.label,
       snippet(articles_fts, 1, '[', ']', ' … ', 12) AS hit
FROM articles_fts
JOIN articles a ON a.id = articles_fts.rowid
JOIN laws l ON l.id = a.law_id
WHERE articles_fts MATCH '"country of origin" AND marking'
ORDER BY rank LIMIT 10;
-- 19 CFR | 134.11 | § 134.11 Country of origin marking required. | … shall be marked …
```

A section with its hierarchy, top down:

```sql
WITH RECURSIVE up(id, parent_id, heading, rubric, depth) AS (
  SELECT d.id, d.parent_id, d.heading, d.rubric, d.depth
  FROM articles a JOIN laws l ON l.id = a.law_id
  JOIN divisions d ON d.id = a.division_id
  WHERE l.slug = 'usc-42' AND a.article_key = '1983'
  UNION ALL
  SELECT d.id, d.parent_id, d.heading, d.rubric, d.depth
  FROM divisions d JOIN up ON d.id = up.parent_id)
SELECT heading, rubric FROM up ORDER BY depth;
-- Chapter 21 | CIVIL RIGHTS
-- Subchapter I | GENERALLY
```

One subsection, by its citation path (40 CFR 141.86(c)(2)):

```sql
SELECT p.label, p.text
FROM article_parts p JOIN articles a ON a.id = p.article_id JOIN laws l ON l.id = a.law_id
WHERE l.slug = 'cfr-40' AND a.article_key = '141.86' AND p.label LIKE '(c)(2)%'
ORDER BY p.seq;
```

Who cites a section (the regulations and laws that cite 19 U.S.C. 1592):

```sql
SELECT fl.nombre_corto, a.article_key, a.label, x.quote
FROM cross_references x
JOIN laws tl ON tl.id = x.target_law_id
JOIN articles a ON a.id = x.article_id
JOIN laws fl ON fl.id = a.law_id
WHERE tl.slug = 'usc-19' AND x.target_article_key = '1592'
ORDER BY fl.id, a.seq;
```

What a section cites, resolved or not:

```sql
SELECT x.quote, x.ref_kind, tl.nombre_corto, x.target_article_key, x.resolved
FROM cross_references x
JOIN articles a ON a.id = x.article_id JOIN laws l ON l.id = a.law_id
LEFT JOIN laws tl ON tl.id = x.target_law_id
WHERE l.slug = 'cfr-19' AND a.article_key = '102.20'
ORDER BY x.seq;
```

A section's amendment history (USC notes):

```sql
SELECT n.action, n.text
FROM annotations n JOIN articles a ON a.id = n.article_id JOIN laws l ON l.id = a.law_id
WHERE l.slug = 'usc-9' AND a.article_key = '2' ORDER BY n.seq;
```

A table of contents with section counts:

```sql
SELECT depth, heading, rubric, descendant_article_count
FROM v_toc WHERE law_slug = 'usc-26' AND depth <= 1 ORDER BY seq;
```

Public Laws approved in a month:

```sql
SELECT nombre_corto, nombre, dof_original FROM laws
WHERE slug LIKE 'plaw-%' AND dof_original LIKE '2025-07%' ORDER BY dof_original;
```

Repealed and reserved sections are kept as placeholders (`[Repealed. ...]`, `[Reserved]`)
so citations to them resolve; to leave them out, add `AND a.text NOT LIKE '[%'`.

## Release assets

| Asset | Size | Unpacked | Contents |
|---|---:|---:|---|
| `us-federal-law.sqlite.zst` | 724 MB | 2.7 GB | all four corpora in one database |
| `usfl-constitution.sqlite.zst` | 0.1 MB | 0.7 MB | the Constitution |
| `usfl-usc.sqlite.zst` | 181 MB | 722 MB | the United States Code |
| `usfl-ecfr.sqlite.zst` | 528 MB | 1.97 GB | the eCFR |
| `usfl-plaw.sqlite.zst` | 12.9 MB | 54 MB | the Public Laws |
| `usfl-<corpus>-articles.jsonl.zst` | 17 KB to 120 MB | 64 KB to 762 MB | one JSON object per article, per corpus |
| `usfl-citations.csv.zst` | 3.6 MB | 43 MB | the citation graph: from law and key, quote, kind, to law and key, resolved |
| `laws.csv` | 170 KB | | the catalog: one row per law with its edition, source URL and sha256 |
| `SHA256SUMS` | | | sha256 of every asset |

Decompress with `zstd -d <file>`. Check with `sha256sum -c SHA256SUMS` (or `shasum -a 256 -c`).
Each per-corpus database holds that corpus's text plus the catalog of all laws, so citations
into other corpora keep their target; its `v_laws` lists every law, with `article_count` 0
for the laws of other corpora. Each JSONL line is one article:
`{"law", "cite", "key", "label", "hierarchy": [...], "text", "last_reform_date", "char_count", "text_sha256"}`.

`samples/` holds small JSONL extracts in the same format (Title 9 of the Code, the first 40
sections of 1 CFR, Pub. L. 118-5) to see the shape without downloading anything.

## How it is built

```
manifests/*.json  -- usfl fetch -->  source/  -- usfl build -->  dist/us-federal-law.sqlite
   (URL + sha256)    (verifies)       (raw)     (re-verifies)            |
                                                              usfl verify, usfl export
                                                                         |
                                                                   dist/release/
```

- `manifests/` pins every source file: its URL, size and sha256, and the edition (release
  point, eCFR date). `python3 -m usfl fetch <corpus>` downloads what is missing and checks
  every file against its hash; a mismatch stops it. `--refresh` discovers the current edition
  and re-pins the manifest.
- `python3 -m usfl build` checks each file's hash again, then maps it: `usfl/uslm.py` for the
  Code and the Public Laws (a streaming walker over USLM XML), `usfl/ecfr.py` for the eCFR,
  `usfl/constitution.py` for the Constitution (`pdftotext -layout` over the two PDFs; its
  output is committed, so only a re-map needs poppler).
- `python3 -m usfl verify` checks foreign keys, the FTS index, unique keys, empty text, law
  counts and source hashes against the manifests, and known sections.
- `python3 -m usfl export` writes the release assets and `SHA256SUMS`.

Python 3.12 standard library only (`uv run python -m usfl ...` works too). Defaults are
overridable: `USFL_SOURCE_DIR`, `USFL_DIST_DIR`, `USFL_MANIFEST_DIR`, `USFL_OLRC_BASE`,
`USFL_ECFR_BASE`, `USFL_GOVINFO_BASE`, `USFL_CONGRESSES`. No credentials are needed: the
GovInfo paths used are its keyless bulk-data and content URLs. A request to
`api.govinfo.gov`, if you add one, sends `GOVINFO_API_KEY` from the environment
(default `DEMO_KEY`) as a header, never in a URL or a file.

To refresh to a new edition: `python3 -m usfl fetch all --refresh`, then build, verify,
export, and commit the changed manifests with a changelog entry.

### Why the Code is not in git

Only the Constitution's text is committed (`data/constitution/`, about 160 KB). The rest is
measured in hundreds of megabytes — 157 MB of USC section text plus 156 MB of USC notes, and
636 MB of CFR text — and a new release point changes many titles at once, so every refresh
would add hundreds of megabytes to the history. It ships as release assets instead; git holds
the code, the schema, the manifests that pin the sources, and small samples.

## Relation to leyes-federales-mx

Both repositories build the same schema, so a Mexican and a US database can be attached
side by side:

```sql
ATTACH 'leyes-federales-mx.sqlite' AS mx;
SELECT 'US' AS j, l.nombre_corto, a.label FROM articles a JOIN laws l ON l.id = a.law_id
 WHERE a.id IN (SELECT rowid FROM main.articles_fts WHERE articles_fts MATCH 'customs AND broker')
UNION ALL
SELECT 'MX', l.nombre_corto, a.label FROM mx.articles a JOIN mx.laws l ON l.id = a.law_id
 WHERE a.id IN (SELECT rowid FROM mx.articles_fts WHERE articles_fts MATCH 'agente AND aduanal');
-- 180 US articles and 64 Mexican ones
```

The column names come from the Mexican side (see the glossary). Values follow each country's
law: `tipo` is `codigo` for the US Code and `reglamento` for the CFR, as for their Mexican
counterparts.

## Limitations

- **Not an official edition.** For legal purposes rely on the official publications. Only
  the titles of the Code enacted as positive law are themselves legal evidence of the law;
  the rest are prima facie evidence (1 U.S.C. 204). The eCFR is an authoritative but
  unofficial compilation of the CFR.
- **One edition each.** This release holds one capture per law; `law_versions`,
  `version_diffs` and `article_changes` are ready for several, but the history of the Code
  is not here.
- **Public Laws are as enacted.** A Public Law row is the Act as signed; later amendments
  live in the Code, not in the Act, and spent or temporary provisions (appropriations for a
  fiscal year) remain as enacted. `estado_vigencia` says `vigente` for every law, which for
  a Public Law means "enacted and not as a whole repealed", not "every provision in force".
- **Text, not typography.** Tables are flattened to `cell | cell` lines, images and
  formulas are omitted, and footnotes and sidenotes are left out of the text. Some
  appendices that are only forms have no text (`[No text]`).
- **Citations are what the sources link or what simple patterns match.** USC and Public Law
  references come from USLM links; CFR references from patterns (`42 U.S.C. 1983`,
  `40 CFR 141.86`, `§ 141.84`, `Public Law 118-5`). Ranges (`§§ 1.1 through 1.9`) count only
  their first section; references to the Statutes at Large, to earlier Congresses' Public
  Laws and to Executive Orders stay `unresolved` with their quote. A CFR reference to a
  section by a prefix the CFR does not use (`§ 1.401` for the `1.401(a)-1` family) does not
  resolve.
- **CFR paragraph paths are inferred.** The eCFR marks paragraphs as flat text; the levels
  `(a)(1)(i)(A)` are reconstructed from the label sequence and italics, which can misplace a
  level in unusual drafting.
- **Constitution signatures.** The attestation clause is included; the signers' names, the
  marginal note on interlineations, and the Convention's resolutions that follow in the
  literal print are not.
- **No states, no case law, no Statutes at Large, no daily Federal Register.**

## Licence

Code: [MIT](LICENSE). Data: [CC0-1.0](LICENSE-DATA) for the compilation; the government
texts themselves carry no copyright (17 U.S.C. 105).

## Citation

See [`CITATION.cff`](CITATION.cff). Please cite the release you used and its edition dates.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Data errors are best reported with the
[data error template](https://github.com/bajalabs/us-federal-law/issues/new?template=data_error.yml):
the law, the article key, the release, and the official source's URL. Security issues:
[SECURITY.md](SECURITY.md).
