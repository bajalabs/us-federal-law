import unittest
from pathlib import Path

from usfl.ecfr import Labeler, map_ecfr, parse_dates

FIX = Path(__file__).parent / "fixtures"


class LabelerTest(unittest.TestCase):
    def labels(self, tokens):
        lab = Labeler()
        return [lab.push(t) for t in tokens]

    def test_standard_levels(self):
        self.assertEqual(self.labels(["a", "1", "i", "A", "B", "ii", "2", "b"]),
                         ["(a)", "(a)(1)", "(a)(1)(i)", "(a)(1)(i)(A)", "(a)(1)(i)(B)",
                          "(a)(1)(ii)", "(a)(2)", "(b)"])

    def test_i_after_h_is_a_letter(self):
        self.assertEqual(self.labels(["h", "i", "j"])[-2:], ["(i)", "(j)"])
        lab = Labeler()
        for t in ["a", "b", "c", "d", "e", "f", "g", "h"]:
            lab.push(t)
        self.assertEqual(lab.push("i"), "(i)")
        self.assertEqual(lab.push("1"), "(i)(1)")

    def test_i_under_a_number_is_roman(self):
        self.assertEqual(self.labels(["a", "1", "i", "ii", "iii", "iv", "v"])[-1], "(a)(1)(v)")

    def test_italic_levels(self):
        lab = Labeler()
        for t in ["a", "1", "i", "A"]:
            lab.push(t)
        self.assertEqual(lab.push("1", italic=True), "(a)(1)(i)(A)(1)")
        self.assertEqual(lab.push("2"), "(a)(2)")


class MapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = map_ecfr(FIX / "ecfr-title.xml")
        cls.arts = {a["article_key"]: a for a in cls.m["articles"]}

    def test_divisions(self):
        d = self.m["divisions"]
        self.assertEqual([x["kind"] for x in d], ["chapter", "part", "subpart"])
        self.assertEqual((d[1]["heading"], d[1]["rubric"]),
                         ("Part 141", "NATIONAL PRIMARY DRINKING WATER REGULATIONS"))
        self.assertEqual(d[2]["path"], "chapter-i/part-141/subpart-i")

    def test_section(self):
        a = self.arts["141.86"]
        self.assertEqual(a["label"], "§ 141.86 Monitoring requirements for lead and copper in tap water.")
        self.assertEqual(a["numero_int"], 141)
        self.assertEqual(a["last_reform_date"], "2024-10-30")
        self.assertNotIn("56 FR 26548", a["text"])          # CITA is an annotation

    def test_paragraph_paths(self):
        parts = self.arts["141.86"]["parts"]
        self.assertEqual([p["label"] for p in parts],
                         ["(a)(1)", "(a)(1)(i)", "(a)(1)(ii)", "(a)(2)", "(b)", "(b)(1)"])
        self.assertIn("Unlabeled text continues paragraph (a)(2).", parts[3]["text"])

    def test_reserved_and_appendix(self):
        self.assertEqual(self.arts["141.87"]["text"], "[Reserved]")
        app = self.arts["appendix-a-to-part-141"]
        self.assertIsNone(app["numero_int"])
        self.assertIn("Lead | Method 200.8", app["text"])
        self.assertEqual(app["division_seq"], 2)            # the part, not the subpart

    def test_annotations(self):
        kinds = sorted(n["target_kind"] for n in self.m["annotations"])
        self.assertEqual(kinds, ["authority", "citation", "source"])

    def test_parse_dates(self):
        self.assertEqual(parse_dates("[56 FR 26548, June 7, 1991; 89 FR 1, Sept. 3, 2024]"),
                         ["1991-06-07", "2024-09-03"])


if __name__ == "__main__":
    unittest.main()
