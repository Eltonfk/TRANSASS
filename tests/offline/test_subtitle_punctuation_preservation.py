"""Regression tests for source punctuation preservation.

Model output must not invent sentence punctuation that is absent from the
source event.  This is especially important for opening/ending lyrics.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "subtranslate"))

from pipeline_v2_1_3 import preserve_source_punctuation_profile  # noqa: E402


class SourcePunctuationPreservationTests(unittest.TestCase):
    def test_unpunctuated_source_removes_invented_comma_and_period(self):
        result, changed = preserve_source_punctuation_profile(
            "sore ja iya datte nandomo naiteru",
            "Choro tantas vezes, porque não gosto disso.",
        )
        self.assertTrue(changed)
        self.assertEqual(result, "Choro tantas vezes porque não gosto disso")

    def test_source_punctuation_class_is_preserved(self):
        result, changed = preserve_source_punctuation_profile("Você está bem?", "Você está bem?")
        self.assertFalse(changed)
        self.assertEqual(result, "Você está bem?")

    def test_ass_tags_and_lexical_hyphen_apostrophe_are_preserved(self):
        result, _ = preserve_source_punctuation_profile(
            r"{\i1}d'água guarda-chuva{\i0}",
            r"{\i1}d'água, guarda-chuva.{\i0}",
        )
        self.assertEqual(result, r"{\i1}d'água guarda-chuva{\i0}")

    def test_comma_is_allowed_when_source_contains_comma_but_period_is_not(self):
        result, _ = preserve_source_punctuation_profile("uma coisa, outra coisa", "uma coisa, outra coisa.")
        self.assertEqual(result, "uma coisa, outra coisa")


if __name__ == "__main__":
    unittest.main()
