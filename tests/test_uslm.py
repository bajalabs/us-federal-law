import unittest
from pathlib import Path

from usfl.uslm import disambiguate_keys, map_uslm

FIX = Path(__file__).parent / "fixtures"


class USCTitleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = map_uslm(FIX / "usc-title.xml")
        cls.arts = {a["article_key"]: a for a in cls.m["articles"]}

    def test_title_metadata(self):
        self.assertEqual(self.m["meta"]["title_heading"], "ARBITRATION")
        self.assertEqual(self.m["meta"]["is-positive-law"], "yes")

    def test_divisions_tree(self):
        divs = self.m["divisions"]
        self.assertEqual([d["kind"] for d in divs], ["chapter", "chapter", "subchapter"])
        self.assertEqual(divs[0]["heading"], "Chapter 1")
        self.assertEqual(divs[0]["rubric"], "GENERAL PROVISIONS")
        sub = divs[2]
        self.assertEqual(sub["parent_seq"], divs[1]["seq"])
        self.assertEqual(sub["depth"], 1)
        self.assertEqual(sub["path"], "chapter-4/subchapter-i")

    def test_sections_only_outside_notes(self):
        # the quoted section inside a note must not become an article
        self.assertEqual(list(self.arts), ["2", "3", "401", "402"])

    def test_section_text_and_label(self):
        a = self.arts["2"]
        self.assertEqual(a["label"], "§ 2. Validity, irrevocability, and enforcement of agreements to arbitrate")
        self.assertIn("shall be valid, irrevocable, and enforceable", a["text"])
        self.assertNotIn("Editorial Notes", a["text"])
        self.assertEqual(a["last_reform_date"], "2022-03-03")
        self.assertEqual(a["division_seq"], 1)

    def test_repealed_placeholder(self):
        a = self.arts["3"]
        self.assertEqual(a["status"], "repealed")
        self.assertTrue(a["text"].startswith("[Repealed."))

    def test_inline_heading_joins_without_space(self):
        a = self.arts["401"]
        self.assertIn("(1) Predispute arbitration agreement.— The term", a["text"])
        self.assertTrue(a["text"].startswith("In this chapter:"))

    def test_parts_paths_and_continuation(self):
        parts = self.arts["402"]["parts"]
        self.assertEqual([p["label"] for p in parts], ["(a)", "(a)(1)", "(a)(2)", "(b)"])
        self.assertEqual(parts[0]["kind"], "subsection")
        self.assertIn("Notwithstanding any other provision", parts[0]["text"])
        self.assertIn("with respect to a case under section 401", parts[0]["text"])
        self.assertEqual(parts[1]["kind"], "paragraph")
        # parts add up to the text: every part's words appear in the article
        for p in parts:
            for chunk in p["text"].split("\n\n"):
                self.assertIn(chunk, self.arts["402"]["text"])

    def test_refs_exclude_notes_and_credits(self):
        hrefs = [r["href"] for r in self.arts["2"]["refs"]]
        self.assertEqual(hrefs, ["/us/usc/t9/ch4"])
        self.assertEqual(self.arts["401"]["refs"][0]["quote"], "section 2246 of title 18")

    def test_annotations(self):
        kinds = [(n["target_kind"], n["action"]) for n in self.m["annotations"]]
        self.assertIn(("source-credit", None), kinds)
        self.assertIn(("note", "amendments"), kinds)
        self.assertNotIn(("note", "editorialNotes"), kinds)   # cross headings are dropped


class USCAppendixTest(unittest.TestCase):
    def test_appendix_root_and_key_disambiguation(self):
        m = map_uslm(FIX / "usc-appendix.xml")
        self.assertEqual(m["meta"]["title_num"], "Title 28—APPENDIX")
        arts = m["articles"]
        disambiguate_keys(arts)
        keys = [a["article_key"] for a in arts]
        self.assertEqual(keys, ["civil-rule-1", "civil-rule-1~2", "pl/91/538/s1", "pl/96/456/s1"])
        kinds = {d["kind"] for d in m["divisions"]}
        self.assertEqual(kinds, {"court-rules", "title", "compiledAct"})


class PublicLawTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = map_uslm(FIX / "plaw.xml")
        disambiguate_keys(cls.m["articles"])
        cls.arts = {a["article_key"]: a for a in cls.m["articles"]}

    def test_metadata(self):
        meta = self.m["meta"]
        self.assertEqual(meta["short_title"], "Consolidated Appropriations Act, 2024")
        self.assertEqual(meta["approvedDate"], "2024-03-09")
        self.assertTrue(meta["official_title"].startswith("Making consolidated appropriations"))
        self.assertNotIn("Mar. 9, 2024", meta["official_title"])   # sidenotes are not text

    def test_repeated_section_numbers_take_synthesized_identifiers(self):
        self.assertIn("dA/tI/s101", self.arts)
        self.assertIn("dB/tI/s101", self.arts)
        self.assertNotIn("Contracts.", self.arts["dA/tI/s101"]["text"])

    def test_appropriations_paragraph_is_an_article(self):
        a = self.arts["appropriation-1"]
        self.assertEqual(a["label"], "Military Construction, Army")
        self.assertIn("$2,022,775,000", a["text"])
        # a heading-only appropriations block is not an article
        self.assertNotIn("appropriation-2", self.arts)

    def test_divisions(self):
        self.assertEqual([(d["kind"], d["ordinal_label"]) for d in self.m["divisions"]],
                         [("division", "A"), ("title", "I"), ("division", "B"), ("title", "I")])
        self.assertEqual(self.m["divisions"][1]["path"], "division-a/title-i")

    def test_refs(self):
        hrefs = [r["href"] for r in self.arts["dB/tI/s101"]["refs"]]
        self.assertEqual(hrefs, ["/us/usc/t7/s2209b", "/us/pl/118/5/s2"])


if __name__ == "__main__":
    unittest.main()
