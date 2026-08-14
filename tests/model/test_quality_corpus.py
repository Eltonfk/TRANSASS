import json
import unittest
from pathlib import Path


class QualityCorpusTests(unittest.TestCase):
    def test_corpus_has_unique_ids_and_review_criteria(self):
        corpus_file = Path(__file__).with_name("quality_corpus_en_ptbr.json")
        corpus = json.loads(corpus_file.read_text(encoding="utf-8"))

        self.assertGreaterEqual(corpus["version"], 1)
        self.assertGreaterEqual(len(corpus["cases"]), 5)
        identifiers = [case["id"] for case in corpus["cases"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        for case in corpus["cases"]:
            self.assertTrue(case["source"])
            self.assertTrue(case["expected_intent"])
            self.assertIn("must_not_contain", case)


if __name__ == "__main__":
    unittest.main()
