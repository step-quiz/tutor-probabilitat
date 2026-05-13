"""
Tests dels verificadors numèrics deterministes (`_check_integer`,
`_check_numeric`, `_check_set`) i de l'heurística `_has_math_content`.

Aquests són els components nous introduïts respecte a `tutor-grups`
(vegeu briefing §4b i §5). Es testegen aïllats, sense aixecar una
sessió sencera.
"""

import unittest

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tutor as T


class TestCheckInteger(unittest.TestCase):

    def test_correct_match(self):
        self.assertTrue(T._check_integer("21", 21))

    def test_incorrect_value(self):
        self.assertFalse(T._check_integer("20", 21))

    def test_with_whitespace(self):
        self.assertTrue(T._check_integer("  21  ", 21))

    def test_with_decimal_zero(self):
        # "21.0" hauria de comptar com a enter
        self.assertTrue(T._check_integer("21.0", 21))

    def test_non_integer_returns_none(self):
        # "21.5" no és enter → delega a la IA
        self.assertIsNone(T._check_integer("21.5", 21))

    def test_unparseable_returns_none(self):
        self.assertIsNone(T._check_integer("vint-i-un", 21))


class TestCheckNumeric(unittest.TestCase):
    """Verificador per a input_type ∈ {"decimal", "fraction"}."""

    def test_decimal_correct(self):
        self.assertTrue(T._check_numeric("0.038", 0.038))

    def test_decimal_with_comma(self):
        self.assertTrue(T._check_numeric("0,038", 0.038))

    def test_decimal_incorrect(self):
        self.assertFalse(T._check_numeric("0.05", 0.038))

    def test_fraction_correct(self):
        self.assertTrue(T._check_numeric("9/19", "9/19"))

    def test_fraction_equivalent_decimal(self):
        # 9/19 ≈ 0.47368421...
        self.assertTrue(T._check_numeric("0.4737", "9/19"))

    def test_fraction_unreduced(self):
        # 18/38 = 9/19. Han de comparar bé.
        self.assertTrue(T._check_numeric("18/38", "9/19"))

    def test_fraction_incorrect(self):
        self.assertFalse(T._check_numeric("9/20", "9/19"))

    def test_unparseable_returns_none(self):
        self.assertIsNone(T._check_numeric("no ho sé", 0.038))

    def test_empty_returns_none(self):
        self.assertIsNone(T._check_numeric("", 0.038))

    def test_division_by_zero_returns_none(self):
        self.assertIsNone(T._check_numeric("3/0", 0.5))

    def test_tolerance_within_bounds(self):
        # 5e-7 << 1e-4 (tolerància) → True
        self.assertTrue(T._check_numeric("0.0380005", 0.038))

    def test_tolerance_outside_bounds(self):
        # 1e-3 > 1e-4 → False
        self.assertFalse(T._check_numeric("0.039", 0.038))


class TestCheckSet(unittest.TestCase):

    def test_correct_set_with_braces(self):
        self.assertTrue(T._check_set("{HH, HT, TH}", ["HH", "HT", "TH"]))

    def test_correct_set_no_braces(self):
        self.assertTrue(T._check_set("HH HT TH", ["HH", "HT", "TH"]))

    def test_case_insensitive(self):
        self.assertTrue(T._check_set("hh, ht, th", ["HH", "HT", "TH"]))

    def test_incorrect_missing_element(self):
        self.assertFalse(T._check_set("{HH, HT}", ["HH", "HT", "TH"]))

    def test_incorrect_extra_element(self):
        self.assertFalse(T._check_set("{HH, HT, TH, TT}", ["HH", "HT", "TH"]))

    def test_empty_returns_none(self):
        self.assertIsNone(T._check_set("", ["HH"]))


class TestHasMathContent(unittest.TestCase):
    """Heurística per a la detecció d'ús inadequat."""

    def test_digits_pass(self):
        self.assertTrue(T._has_math_content("21"))
        self.assertTrue(T._has_math_content("0.5"))

    def test_operators_pass(self):
        self.assertTrue(T._has_math_content("P(A) + P(B)"))
        self.assertTrue(T._has_math_content("3/8"))

    def test_keywords_pass_ca(self):
        self.assertTrue(T._has_math_content("probabilitat condicionada"))
        self.assertTrue(T._has_math_content("teorema de Bayes"))
        self.assertTrue(T._has_math_content("espai mostral"))

    def test_keywords_pass_en(self):
        self.assertTrue(T._has_math_content("conditional probability"))
        self.assertTrue(T._has_math_content("binomial distribution"))

    def test_chat_input_fails(self):
        self.assertFalse(T._has_math_content("hola"))
        self.assertFalse(T._has_math_content("com va"))
        self.assertFalse(T._has_math_content("ajuda'm"))

    def test_empty_fails(self):
        self.assertFalse(T._has_math_content(""))
        self.assertFalse(T._has_math_content("   "))
        self.assertFalse(T._has_math_content(None))


if __name__ == "__main__":
    unittest.main()
