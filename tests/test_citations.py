import unittest

from usfl.citations import classify, target_from_href, text_citations


class HrefTest(unittest.TestCase):
    def test_targets(self):
        self.assertEqual(target_from_href("/us/usc/t42/s1983/a"), ("usc-42", "1983"))
        self.assertEqual(target_from_href("/us/usc/t15/s78q–1"), ("usc-15", "78q-1"))
        self.assertEqual(target_from_href("/us/usc/t9/ch4"), ("usc-9", None))
        self.assertEqual(target_from_href("/us/pl/118/5/s2"), ("plaw-118-5", "2"))
        self.assertEqual(target_from_href("/us/cfr/t40/s141.86"), ("cfr-40", "141.86"))
        self.assertEqual(target_from_href("/us/cfr/t40/pt141"), ("cfr-40", None))
        self.assertIsNone(target_from_href("/us/stat/136/27"))


class TextTest(unittest.TestCase):
    def test_patterns(self):
        text = ("See 42 U.S.C. 300g-1, 40 CFR 141.89, 40 CFR part 141, § 141.84, "
                "title 7, United States Code, and Public Law 118–5.")
        got = [(c["quote"], c["target"]) for c in text_citations(text, "cfr-40")]
        self.assertEqual(got, [
            ("42 U.S.C. 300g-1", ("usc-42", "300g-1")),
            ("40 CFR 141.89", ("cfr-40", "141.89")),
            ("40 CFR part 141", ("cfr-40", None)),
            ("§ 141.84", ("cfr-40", "141.84")),
            ("title 7, United States Code", ("usc-7", None)),
            ("Public Law 118–5", ("plaw-118-5", None)),
        ])


class ClassifyTest(unittest.TestCase):
    law_ids = {"cfr-40": 1, "usc-42": 2, "us-const": 3}
    keys = {1: {"141.84"}, 2: {"1983"}, 3: {"amd-1"}}

    def c(self, own, tgt):
        return classify(own, tgt, self.law_ids, self.keys)

    def test_kinds(self):
        self.assertEqual(self.c("cfr-40", ("cfr-40", "141.84"))["ref_kind"], "self-article")
        self.assertEqual(self.c("cfr-40", ("usc-42", "1983"))["resolved"], 1)
        r = self.c("cfr-40", ("usc-42", "9999"))
        self.assertEqual((r["ref_kind"], r["target_law_id"], r["resolved"]), ("law", 2, 0))
        self.assertEqual(self.c("usc-42", ("us-const", None))["ref_kind"], "constitution")
        r = self.c("cfr-40", ("plaw-89-554", None))
        self.assertEqual((r["ref_kind"], r["target_law_id"]), ("unresolved", None))
        self.assertEqual(self.c("cfr-40", None)["ref_kind"], "unresolved")


if __name__ == "__main__":
    unittest.main()
