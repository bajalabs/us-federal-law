-- ═══════════════════════════════════════════════════════════════════════════
-- us-federal-law — the in-force US federal law stack as a SQLite database
--
-- The same schema as the sister repository leyes-federales-mx (Mexican federal
-- laws): every table, column and view below is defined identically there, so
-- the two databases load side by side and one query works on both. Column names
-- are shared verbatim, including the Spanish ones; README.md has a glossary.
--
--  * A LAW has a stable identity (the Constitution, one USC title or appendix,
--    one CFR title, one Public Law); a LAW_VERSION is one captured edition of it.
--    Everything derived hangs off a law_version, never off the law.
--  * ARTICLES are the citable units: Constitution sections and amendments, USC
--    sections and court rules, CFR sections and appendices, Public Law sections.
--  * Every text-bearing row carries text_sha256, so comparing two editions is a
--    hash comparison first.
--  * The core tables use plain types. The FTS5 table is the one SQLite-specific
--    piece.
-- ═══════════════════════════════════════════════════════════════════════════

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jurisdictions (
  code        TEXT PRIMARY KEY,          -- 'US' (the sister repository: 'MX')
  name        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corpora (
  id              INTEGER PRIMARY KEY,
  jurisdiction    TEXT NOT NULL REFERENCES jurisdictions(code),
  slug            TEXT NOT NULL,         -- 'us-constitution' | 'us-code' | 'ecfr' | 'public-laws'
  name            TEXT NOT NULL,
  source_url      TEXT,
  UNIQUE (jurisdiction, slug)
);

-- Stable identity of a legal instrument.
CREATE TABLE IF NOT EXISTS laws (
  id                INTEGER PRIMARY KEY,
  corpus_id         INTEGER NOT NULL REFERENCES corpora(id),
  slug              TEXT NOT NULL,       -- 'us-const' | 'usc-42' | 'usc-28a' | 'cfr-40' | 'plaw-118-42'
  numero            TEXT,                -- '42' | '28a' | '40' | '118-42'
  nombre            TEXT NOT NULL,
  nombre_corto      TEXT,                -- the citation form: 'U.S. Const.' | '42 U.S.C.' | '28 U.S.C. App.' | '40 CFR' | 'Pub. L. 118-42'
  codigo_archivo    TEXT,                -- source file id: 'usc42' | 'title-40' | 'PLAW-118publ42' | 'GPO-CONAN-2022'
  tipo              TEXT,                -- constitucion (Constitution) | codigo (USC) | reglamento (CFR) | ley (Public Law)
  estado_vigencia   TEXT,                -- vigente (in force); see README for what that means per corpus
  dof_original      TEXT,                -- original enactment date; US: a Public Law's approval date (ISO)
  url_detalle       TEXT,                -- the publisher's page or file for the law
  UNIQUE (corpus_id, slug)
);

-- One captured edition. Two captures with the same sha256 are the same version.
CREATE TABLE IF NOT EXISTS law_versions (
  id                INTEGER PRIMARY KEY,
  law_id            INTEGER NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
  captured_at       TEXT NOT NULL,
  ultima_reforma    TEXT,                -- latest amendment reflected, as the edition states it: 'Public Law 119-138'
  reforma_date      TEXT,                -- that date, ISO and sortable
  ultima_reforma_indice TEXT,            -- what the publisher's index said when read: release point, eCFR 'up to date as of'
  source_kind       TEXT NOT NULL,       -- 'olrc-uslm-xml' | 'ecfr-versioner-full-xml' | 'govinfo-plaw-uslm-xml' | 'gpo-conan-literal-print-pdf'
  resolved_url      TEXT,
  source_sha256     TEXT NOT NULL,       -- pins the official file the text came from
  source_bytes      INTEGER,
  converter         TEXT,                -- provenance of the extraction
  text_sha256       TEXT,                -- hash of the full converted text
  UNIQUE (law_id, source_kind, source_sha256)
);
CREATE INDEX IF NOT EXISTS ix_lv_law ON law_versions(law_id, reforma_date);

-- Subtitle / chapter / subchapter / part / subpart / ... — a self-referencing tree.
CREATE TABLE IF NOT EXISTS divisions (
  id              INTEGER PRIMARY KEY,
  law_version_id  INTEGER NOT NULL REFERENCES law_versions(id) ON DELETE CASCADE,
  parent_id       INTEGER REFERENCES divisions(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL,         -- subtitle|chapter|subchapter|part|subpart|division|title|subject-group|article|amendment|...
  ordinal_label   TEXT,                  -- '21', 'I', 'A'
  heading         TEXT NOT NULL,         -- 'Chapter 21'
  rubric          TEXT,                  -- 'CIVIL RIGHTS'
  depth           INTEGER NOT NULL,
  seq             INTEGER NOT NULL,      -- document order
  path            TEXT                   -- 'chapter-21/subchapter-i'
);
CREATE INDEX IF NOT EXISTS ix_div_lv ON divisions(law_version_id, seq);

CREATE TABLE IF NOT EXISTS articles (
  id              INTEGER PRIMARY KEY,
  law_version_id  INTEGER NOT NULL REFERENCES law_versions(id) ON DELETE CASCADE,
  law_id          INTEGER NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
                  -- denormalised from law_versions.law_id: the one-hop answer
                  -- to "which law does this article belong to"
  division_id     INTEGER REFERENCES divisions(id) ON DELETE SET NULL,
  article_key     TEXT NOT NULL,         -- stable across editions: '1983', '78u-4', '141.86', 'I-8', 'amd-14-1', 'civil-rule-12'
  numero_int      INTEGER,               -- leading integer of the key (1983; 141 for '141.86'), NULL when none
  suffix          TEXT,                  -- what follows that integer: 'u-4' in '78u-4'
  label           TEXT NOT NULL,         -- '§ 1983. Civil action for deprivation of rights'
  seq             INTEGER NOT NULL,
  is_transitorio  INTEGER NOT NULL DEFAULT 0,   -- Mexican transitory articles; always 0 here
  decree_group    TEXT,                  -- Mexican decree grouping; NULL here
  text            TEXT NOT NULL,         -- normative text, paragraphs separated by a blank line; notes excluded
  text_sha256     TEXT NOT NULL,
  char_count      INTEGER NOT NULL,
  last_reform_date TEXT,                 -- latest date in its source credit (USC) or FR citation (CFR); NULL = none
  UNIQUE (law_version_id, article_key, seq)
);
CREATE INDEX IF NOT EXISTS ix_art_lv  ON articles(law_version_id, seq);
CREATE INDEX IF NOT EXISTS ix_art_law ON articles(law_id, seq);
CREATE INDEX IF NOT EXISTS ix_art_div ON articles(division_id);
CREATE INDEX IF NOT EXISTS ix_art_key ON articles(article_key);
CREATE INDEX IF NOT EXISTS ix_art_sha ON articles(text_sha256);

-- Structural levels inside an article: USC subsections, paragraphs, clauses...;
-- CFR paragraphs; Constitution clauses. Each part holds its own words, not its
-- children's, so the parts of an article add up to its text.
CREATE TABLE IF NOT EXISTS article_parts (
  id            INTEGER PRIMARY KEY,
  article_id    INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL,           -- subsection | paragraph | subparagraph | clause | ...
  label         TEXT,                    -- the full path: '(a)(1)(A)'; 'cl. 3'
  seq           INTEGER NOT NULL,
  text          TEXT NOT NULL,
  text_sha256   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_part_art ON article_parts(article_id, seq);

-- Editorial matter kept OUT of the normative text and attached to what it
-- annotates: USC source credits and notes; CFR citations, authority, source.
CREATE TABLE IF NOT EXISTS annotations (
  id              INTEGER PRIMARY KEY,
  law_version_id  INTEGER NOT NULL REFERENCES law_versions(id) ON DELETE CASCADE,
  article_id      INTEGER REFERENCES articles(id) ON DELETE CASCADE,
  division_id     INTEGER REFERENCES divisions(id) ON DELETE CASCADE,
  seq             INTEGER NOT NULL,
  target_kind     TEXT,                  -- source-credit | note | citation | authority | source | editorial-note
  action          TEXT,                  -- USC note topic: amendments | effectiveDate | shortTitle | ...
  dof_dates       TEXT,                  -- JSON array of the ISO dates it mentions
  text            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ann_lv  ON annotations(law_version_id, seq);
CREATE INDEX IF NOT EXISTS ix_ann_art ON annotations(article_id);

-- Lossless landing zone of the sister repository. Not filled here: the USLM and
-- eCFR XML sources are structured already and stay pinned in manifests/.
CREATE TABLE IF NOT EXISTS blocks (
  id              INTEGER PRIMARY KEY,
  law_version_id  INTEGER NOT NULL REFERENCES law_versions(id) ON DELETE CASCADE,
  seq             INTEGER NOT NULL,
  kind            TEXT NOT NULL,         -- article|article_ordinal|ordinal_lead|heading|transitorio_heading|annotation|note|body|table
  text            TEXT NOT NULL,
  style           TEXT                   -- JSON: css class, size, colour, alignment, bold ratio
);
CREATE INDEX IF NOT EXISTS ix_blk_lv ON blocks(law_version_id, seq);

-- ── change tracking (empty in this release: one edition per law) ──────────
CREATE TABLE IF NOT EXISTS version_diffs (
  id                INTEGER PRIMARY KEY,
  law_id            INTEGER NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
  from_version_id   INTEGER NOT NULL REFERENCES law_versions(id) ON DELETE CASCADE,
  to_version_id     INTEGER NOT NULL REFERENCES law_versions(id) ON DELETE CASCADE,
  computed_at       TEXT NOT NULL,
  n_added           INTEGER NOT NULL DEFAULT 0,
  n_removed         INTEGER NOT NULL DEFAULT 0,
  n_modified        INTEGER NOT NULL DEFAULT 0,
  n_unchanged       INTEGER NOT NULL DEFAULT 0,
  UNIQUE (from_version_id, to_version_id)
);

CREATE TABLE IF NOT EXISTS article_changes (
  id                INTEGER PRIMARY KEY,
  version_diff_id   INTEGER NOT NULL REFERENCES version_diffs(id) ON DELETE CASCADE,
  article_key       TEXT NOT NULL,
  change_type       TEXT NOT NULL,       -- added | removed | modified | renumbered | unchanged
  from_article_id   INTEGER REFERENCES articles(id) ON DELETE SET NULL,
  to_article_id     INTEGER REFERENCES articles(id) ON DELETE SET NULL,
  similarity        REAL,                -- 0..1, NULL when added/removed
  diff              TEXT                 -- unified diff for 'modified'; old -> new label for 'renumbered'
);
CREATE INDEX IF NOT EXISTS ix_chg_diff ON article_changes(version_diff_id, change_type);

-- ── citation graph ─────────────────────────────────────────────────────────
-- References extracted from article text: USLM links in the Code and the
-- Public Laws, citation patterns in the CFR ('42 U.S.C. 1983', '40 CFR 141.86',
-- '§ 141.84', 'Public Law 118-5'). The quote is verbatim, so every edge carries
-- its evidence. `resolved` says whether the target article was found;
-- ref_kind 'unresolved' (target_* NULL) marks targets outside this corpus.
CREATE TABLE IF NOT EXISTS cross_references (
  id                INTEGER PRIMARY KEY,
  law_version_id    INTEGER NOT NULL REFERENCES law_versions(id) ON DELETE CASCADE,
  article_id        INTEGER REFERENCES articles(id) ON DELETE CASCADE,
  seq               INTEGER NOT NULL,     -- occurrence order within the article
  quote             TEXT NOT NULL,        -- matched span, verbatim
  ref_kind          TEXT NOT NULL,        -- self-article | constitution | law | unresolved
  target_law_id     INTEGER REFERENCES laws(id),
  target_article_key TEXT,                -- NULL when the reference is to a whole law
  resolved          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_xref_lv     ON cross_references(law_version_id);
CREATE INDEX IF NOT EXISTS ix_xref_art    ON cross_references(article_id);
CREATE INDEX IF NOT EXISTS ix_xref_target ON cross_references(target_law_id, target_article_key);

-- ── full-text search (SQLite FTS5) ─────────────────────────────────────────
-- Case- and accent-insensitive tokens (unicode61, remove_diacritics 2).
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
  label, text, article_key UNINDEXED, law_version_id UNINDEXED,
  content='articles', content_rowid='id', tokenize="unicode61 remove_diacritics 2"
);
CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
  INSERT INTO articles_fts(rowid,label,text,article_key,law_version_id)
  VALUES (new.id,new.label,new.text,new.article_key,new.law_version_id);
END;
CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
  INSERT INTO articles_fts(articles_fts,rowid,label,text,article_key,law_version_id)
  VALUES ('delete',old.id,old.label,old.text,old.article_key,old.law_version_id);
END;
CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
  INSERT INTO articles_fts(articles_fts,rowid,label,text,article_key,law_version_id)
  VALUES ('delete',old.id,old.label,old.text,old.article_key,old.law_version_id);
  INSERT INTO articles_fts(rowid,label,text,article_key,law_version_id)
  VALUES (new.id,new.label,new.text,new.article_key,new.law_version_id);
END;

-- ── views: the one-hop surface for queries, GUIs and BI ────────────────────
-- The normalised truth stays articles -> law_versions -> laws. "Current" is
-- the latest law_version per law (reforma_date desc, id desc).
DROP VIEW IF EXISTS v_laws;
CREATE VIEW v_laws AS
SELECT l.id, l.slug, l.numero, l.nombre, l.nombre_corto, l.codigo_archivo,
       l.tipo, l.estado_vigencia, l.dof_original, l.url_detalle,
       c.jurisdiction, c.slug AS corpus_slug,
       v.id AS law_version_id, v.ultima_reforma, v.reforma_date, v.ultima_reforma_indice, v.source_kind,
       v.captured_at, v.resolved_url, v.source_sha256,
       (SELECT count(*) FROM articles a WHERE a.law_version_id = v.id)    AS article_count,
       (SELECT count(*) FROM divisions d WHERE d.law_version_id = v.id)   AS division_count,
       (SELECT count(*) FROM annotations n WHERE n.law_version_id = v.id) AS annotation_count,
       (SELECT count(*) FROM cross_references x WHERE x.law_version_id = v.id) AS crossref_count
FROM laws l
JOIN corpora c ON c.id = l.corpus_id
JOIN law_versions v ON v.id = (
  SELECT v2.id FROM law_versions v2 WHERE v2.law_id = l.id
  ORDER BY v2.reforma_date DESC, v2.id DESC LIMIT 1);

DROP VIEW IF EXISTS v_articles;
CREATE VIEW v_articles AS
SELECT a.id, a.law_id, l.slug AS law_slug, l.nombre AS law_nombre, l.nombre_corto,
       l.tipo AS law_tipo, c.jurisdiction,
       a.law_version_id, a.article_key, a.numero_int, a.suffix, a.label, a.seq,
       a.is_transitorio, a.decree_group, a.text, a.last_reform_date, a.char_count,
       a.division_id,
       d.kind AS division_kind, d.heading AS division_heading,
       d.rubric AS division_rubric, d.path AS division_path
FROM articles a
JOIN laws l ON l.id = a.law_id
JOIN corpora c ON c.id = l.corpus_id
LEFT JOIN divisions d ON d.id = a.division_id;

DROP VIEW IF EXISTS v_toc;
CREATE VIEW v_toc AS
SELECT d.id AS division_id, d.law_version_id, v.law_id, l.slug AS law_slug,
       d.parent_id, d.kind, d.ordinal_label, d.heading, d.rubric, d.depth, d.seq, d.path,
       (SELECT count(*) FROM articles a WHERE a.division_id = d.id) AS article_count,
       (SELECT count(*) FROM articles a
          JOIN divisions d2 ON d2.id = a.division_id
         WHERE d2.law_version_id = d.law_version_id
           AND d.path IS NOT NULL
           AND (d2.id = d.id OR d2.path = d.path OR d2.path LIKE d.path || '/%')
       ) AS descendant_article_count
FROM divisions d
JOIN law_versions v ON v.id = d.law_version_id
JOIN laws l ON l.id = v.law_id
WHERE d.kind IS NOT 'otro';

DROP VIEW IF EXISTS v_reformas;
CREATE VIEW v_reformas AS
SELECT n.id, n.law_version_id, v.law_id, l.slug AS law_slug, l.nombre_corto,
       n.article_id, a.label AS article_label, a.article_key,
       n.division_id, n.target_kind, n.action, n.dof_dates, n.text, n.seq
FROM annotations n
JOIN law_versions v ON v.id = n.law_version_id
JOIN laws l ON l.id = v.law_id
LEFT JOIN articles a ON a.id = n.article_id;
