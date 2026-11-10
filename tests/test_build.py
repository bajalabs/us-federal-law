import sqlite3
import tempfile
import unittest
from pathlib import Path

from usfl.build import Builder, build, smart_title
from usfl.common import DATA_DIR, MANIFEST_DIR, SCHEMA_PATH
from usfl.ecfr import map_ecfr
from usfl.uslm import map_uslm
from usfl.verify import run as verify

FIX = Path(__file__).parent / "fixtures"
VERSION = {"captured_at": "2026-11-10T00:00:00+00:00", "source_kind": "test",
           "source_sha256": "0" * 64}


class ConstitutionBuildTest(unittest.TestCase):
    """The README quick start: build the Constitution with no download."""

    def test_build_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "c.sqlite"
            receipt = build(db, ["constitution"], source_dir=Path(tmp) / "no-source",
                            manifest_dir=MANIFEST_DIR, data_dir=DATA_DIR)
            self.assertEqual(receipt["corpora"]["constitution"]["articles"], 75)
            v = verify(db, MANIFEST_DIR, allow_partial=True)
            self.assertTrue(v["ok"], v["problems"])
            con = sqlite3.connect(db)
            hit = con.execute(
                "SELECT a.article_key FROM articles_fts JOIN articles a ON a.id = articles_fts.rowid "
                "WHERE articles_fts MATCH '\"cruel and unusual\"'").fetchall()
            self.assertEqual(hit, [("amd-8",)])
            row = con.execute("SELECT nombre_corto, article_count FROM v_laws").fetchone()
            self.assertEqual(row, ("U.S. Const.", 75))


class LoaderTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.b = Builder(self.con)

    def test_usc_and_cfr_with_citations(self):
        self.b.load("usc", {"slug": "usc-9", "nombre": "Title 9", "tipo": "codigo",
                            "nombre_corto": "9 U.S.C."}, VERSION, map_uslm(FIX / "usc-title.xml"))
        self.b.load("ecfr", {"slug": "cfr-40", "nombre": "Title 40", "tipo": "reglamento",
                             "nombre_corto": "40 CFR"}, VERSION, map_ecfr(FIX / "ecfr-title.xml"),
                    text_refs=True)
        self.b.resolve_refs()
        con = self.con
        self.assertEqual(con.execute("SELECT count(*) FROM laws").fetchone()[0], 2)
        # hierarchy of a section, by recursive CTE over divisions
        chain = con.execute("""
            WITH RECURSIVE up(id, parent_id, heading, rubric, depth) AS (
              SELECT d.id, d.parent_id, d.heading, d.rubric, d.depth FROM articles a
                JOIN divisions d ON d.id = a.division_id WHERE a.article_key = '401'
              UNION ALL
              SELECT d.id, d.parent_id, d.heading, d.rubric, d.depth FROM divisions d
                JOIN up ON d.id = up.parent_id)
            SELECT heading FROM up ORDER BY depth""").fetchall()
        self.assertEqual(chain, [("Chapter 4",), ("Subchapter I",)])
        parts = con.execute("SELECT p.label FROM article_parts p JOIN articles a ON a.id=p.article_id "
                            "WHERE a.article_key='402' ORDER BY p.seq").fetchall()
        self.assertEqual([p[0] for p in parts], ["(a)", "(a)(1)", "(a)(2)", "(b)"])
        refs = dict(((q, (k, r))) for q, k, r in con.execute(
            "SELECT quote, ref_kind, resolved FROM cross_references"))
        self.assertEqual(refs["chapter 4"], ("law", 1))          # law-level link to its own title
        self.assertEqual(refs["section 401"], ("self-article", 1))
        self.assertEqual(refs["40 CFR 141.89"], ("self-article", 0))   # not in the fixture
        self.assertEqual(refs["section 2246 of title 18"], ("unresolved", 0))
        toc = con.execute("SELECT heading, descendant_article_count FROM v_toc "
                          "WHERE law_slug='usc-9' AND kind='chapter' ORDER BY seq").fetchall()
        self.assertEqual(toc, [("Chapter 1", 2), ("Chapter 4", 2)])
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])


class SmartTitleTest(unittest.TestCase):
    def test_titles(self):
        self.assertEqual(smart_title("THE PUBLIC HEALTH AND WELFARE"), "The Public Health and Welfare")
        self.assertEqual(smart_title("VETERANS’ BENEFITS"), "Veterans’ Benefits")
        self.assertEqual(smart_title("Already Mixed"), "Already Mixed")


if __name__ == "__main__":
    unittest.main()
