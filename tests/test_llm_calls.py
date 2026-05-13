"""
Tests de les crides a la IA (llm.py) amb mocks.
No fa cridades reals a l'API.

Adaptació de `tutor-grups/tests/test_llm_calls.py` al domini de
probabilitat. Comprova que el contract JSON i el fallback de
`diagnose_dependency` segueixen funcionant amb la nova llista de
dependències (`def_prob_condicionada`, etc.).
"""

import json
import unittest
from unittest.mock import patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm


# ---- Fixtures ----
STEP_BAY_2 = {
    "id": 2,
    "text": "Calcula P(D), la probabilitat total d'obtenir una peça defectuosa.",
    "expected_summary": "P(D) = 0.6·0.03 + 0.4·0.05 = 0.038",
    "key_concepts": ["def_prob_total"],
    "input_type": "decimal",
    "expected_value": 0.038,
    "typical_error": "omitting one branch of the total probability expansion",
    "typical_error_label": "TOT_branca_oblidada",
}

PROBLEM_BAY01 = {
    "id": "PROB-BAY-01",
    "dependencies": ["def_prob_condicionada", "def_prob_total", "def_bayes"],
}


def _mock_call_json_returns(payload: dict):
    """Retorna un patcher que fa que _call_json retorni payload com a JSON."""
    return patch("llm._call_json", return_value=json.dumps(payload))


def _mock_call_text_returns(text: str):
    return patch("llm._call_text", return_value=text)


class TestJudgeStep(unittest.TestCase):

    def test_correct_verdict(self):
        payload = {"verdict": "correct", "reason": "Bé!", "error_label": None}
        with _mock_call_json_returns(payload):
            result = llm.judge_step(STEP_BAY_2, "0.038")
        self.assertEqual(result["verdict"], "correct")
        self.assertEqual(result["reason"], "Bé!")
        self.assertIsNone(result["error_label"])

    def test_typical_error_verdict(self):
        payload = {
            "verdict": "typical_error",
            "reason": "Has oblidat una branca al càlcul.",
            "error_label": "TOT_branca_oblidada",
        }
        with _mock_call_json_returns(payload):
            result = llm.judge_step(STEP_BAY_2, "0.018")
        self.assertEqual(result["verdict"], "typical_error")
        self.assertEqual(result["error_label"], "TOT_branca_oblidada")

    def test_conceptual_gap_verdict(self):
        payload = {
            "verdict": "conceptual_gap",
            "reason": "No sap aplicar la probabilitat total.",
            "error_label": None,
        }
        with _mock_call_json_returns(payload):
            result = llm.judge_step(STEP_BAY_2, "No ho sé.")
        self.assertEqual(result["verdict"], "conceptual_gap")

    def test_invalid_verdict_normalized(self):
        """Veredictes desconeguts es normalitzen a typical_error."""
        payload = {"verdict": "unknown_verdict", "reason": "?", "error_label": None}
        with _mock_call_json_returns(payload):
            result = llm.judge_step(STEP_BAY_2, "alguna cosa")
        self.assertEqual(result["verdict"], "typical_error")


class TestDiagnoseDependency(unittest.TestCase):

    def test_returns_valid_dep_id(self):
        payload = {
            "dep_id": "def_prob_total",
            "justification": "Student didn't apply total probability theorem.",
        }
        with _mock_call_json_returns(payload):
            dep = llm.diagnose_dependency(STEP_BAY_2, "0.018", PROBLEM_BAY01)
        self.assertEqual(dep, "def_prob_total")

    def test_invalid_dep_falls_back_to_first(self):
        """Si la IA retorna un dep_id no vàlid, fallback al primer dep del problema."""
        payload = {"dep_id": "dep_inexistent"}
        with _mock_call_json_returns(payload):
            dep = llm.diagnose_dependency(STEP_BAY_2, "no sé.", PROBLEM_BAY01)
        # Ha de retornar el primer dep del problema, no el dep_inexistent
        self.assertEqual(dep, "def_prob_condicionada")


class TestGenerateHint(unittest.TestCase):

    def test_returns_stripped_text(self):
        hint_text = "  Recorda que la probabilitat total suma totes les branques.  "
        with _mock_call_text_returns(hint_text):
            result = llm.generate_hint(STEP_BAY_2, "def_prob_total")
        self.assertEqual(result, "Recorda que la probabilitat total suma totes les branques.")

    def test_unknown_dep_uses_dep_id_as_desc(self):
        with _mock_call_text_returns("Aplica el concepte."):
            result = llm.generate_hint(STEP_BAY_2, "dep_no_existent")
        self.assertEqual(result, "Aplica el concepte.")


class TestExtractJson(unittest.TestCase):
    def test_clean_json(self):
        r = llm._extract_json('{"verdict": "correct"}')
        self.assertEqual(r["verdict"], "correct")

    def test_json_with_fences(self):
        r = llm._extract_json('```json\n{"verdict": "correct"}\n```')
        self.assertEqual(r["verdict"], "correct")

    def test_json_embedded_in_text(self):
        r = llm._extract_json('Some text {"verdict": "error"} more text')
        self.assertEqual(r["verdict"], "error")

    def test_empty_returns_empty_dict(self):
        r = llm._extract_json("")
        self.assertEqual(r, {})


if __name__ == "__main__":
    unittest.main()
