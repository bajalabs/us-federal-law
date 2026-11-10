import json
import unittest
from pathlib import Path

from usfl.constitution import parse, to_markdown

FIX = Path(__file__).parent / "fixtures"
DATA = Path(__file__).resolve().parent.parent / "data" / "constitution" / "constitution.json"


class ParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = parse((FIX / "constitution-body.txt").read_text(encoding="utf-8"),
                      (FIX / "constitution-amendments.txt").read_text(encoding="utf-8"),
                      (FIX / "constitution-attestation.txt").read_text(encoding="utf-8"))
        cls.arts = {a["article_key"]: a for a in cls.m["articles"]}

    def test_keys(self):
        self.assertEqual(list(self.arts), ["preamble", "I-1", "I-2", "V", "VII", "attestation",
                                           "amd-1", "amd-2", "amd-13-1", "amd-13-2"])

    def test_clauses_and_page_break(self):
        a = self.arts["I-2"]
        self.assertEqual(len(a["parts"]), 5)
        self.assertEqual(a["parts"][0]["label"], "cl. 1")
        # the clause carried over a page break is joined, with no running head in it
        cl3 = a["parts"][2]["text"]
        self.assertIn("New Jersey four, Pennsylvania eight", cl3)
        self.assertNotIn("Literal Print", cl3)

    def test_article_without_sections(self):
        self.assertEqual(self.arts["V"]["label"], "Article V")
        self.assertEqual(self.arts["VII"]["numero_int"], 7)
        self.assertNotIn("Erazure", self.arts["VII"]["text"])

    def test_attestation(self):
        self.assertTrue(self.arts["attestation"]["text"].startswith("done in Convention"))

    def test_amendments(self):
        self.assertEqual(self.arts["amd-13-2"]["label"], "Amendment XIII, Section 2")
        div = {d["seq"]: d for d in self.m["divisions"]}
        self.assertEqual(div[self.arts["amd-1"]["division_seq"]]["path"], "amendments/amendment-i")

    def test_markdown(self):
        md = to_markdown(self.m)
        self.assertIn("## Article I", md)
        self.assertIn("### Section 2", md)
        self.assertIn("### Amendment XIII", md)


class CommittedDataTest(unittest.TestCase):
    def test_committed_constitution_is_complete(self):
        m = json.loads(DATA.read_text(encoding="utf-8"))
        keys = {a["article_key"] for a in m["articles"]}
        self.assertIn("preamble", keys)
        self.assertIn("amd-27", keys)
        self.assertEqual(sum(1 for k in keys if k.startswith("amd-")), 49)
        i8 = next(a for a in m["articles"] if a["article_key"] == "I-8")
        self.assertEqual(len(i8["parts"]), 18)


if __name__ == "__main__":
    unittest.main()
