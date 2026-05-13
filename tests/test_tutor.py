"""
Tests de la màquina d'estats (tutor.py).
No requereix Streamlit ni cridades reals a la IA (usa mocks).

Adaptació de `tutor-grups/tests/test_tutor.py` al domini de probabilitat:
- Problema canònic: `PROB-BAY-01` (3 passos: free_text → decimal → fraction).
- Tests addicionals per al verdict `suspended` (ús inadequat).
"""

import unittest
from unittest.mock import patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tutor as T
import problems as PB  # noqa: F401 — importat per coherència amb test_tutor de tutor-grups


class TestNewSessionState(unittest.TestCase):
    def test_initial_state_bay01(self):
        state = T.new_session_state("PROB-BAY-01")
        self.assertEqual(state["problem_id"], "PROB-BAY-01")
        self.assertEqual(state["current_step_idx"], 0)
        self.assertIsNone(state["verdict_final"])
        self.assertIsNone(state["active_prereq"])
        self.assertEqual(state["backtrack_depth"], 0)
        # Camp nou portat de tutor-eq:
        self.assertEqual(state["inappropriate_warnings"], 0)

    def test_initial_state_has_3_steps(self):
        state = T.new_session_state("PROB-BAY-01")
        self.assertEqual(len(state["problem"]["passos"]), 3)


class TestEscapeSignals(unittest.TestCase):
    def setUp(self):
        self.state = T.new_session_state("PROB-BAY-01", student_id="test_alumne")

    def test_exit_signal(self):
        new = T.process_turn(self.state, "!!")
        self.assertEqual(new["verdict_final"], "abandoned")

    def test_exit_signal_variant(self):
        new = T.process_turn(self.state, "exit")
        self.assertEqual(new["verdict_final"], "abandoned")

    def test_discrepancy_signal(self):
        new = T.process_turn(self.state, "!El meu raonament és diferent")
        # Ha de quedar anotat i avançar un pas
        self.assertEqual(len(new["discrepancies"]), 1)
        self.assertEqual(new["discrepancies"][0]["text"], "El meu raonament és diferent")
        self.assertEqual(new["current_step_idx"], 1)

    def test_hint_signal_no_active_prereq(self):
        with patch("llm.generate_hint", return_value="Pensa en la definició de probabilitat condicionada."):
            new = T.process_turn(self.state, "?")
        hint_msgs = [m for m in new["messages"] if m["kind"] == "hint"]
        self.assertEqual(len(hint_msgs), 1)
        self.assertIn("condicionada", hint_msgs[0]["text"].lower())


class TestCorrectPath(unittest.TestCase):
    """Camí C1: l'alumne respon correctament tots els passos de PROB-BAY-01."""

    def test_correct_answers_advance_step(self):
        state = T.new_session_state("PROB-BAY-01", student_id="C1")
        # Pas 1 = free_text → IA. Pas 2 = decimal (0.038) → determinista.
        # Pas 3 = fraction ("9/19") → determinista.
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {"verdict": "correct", "reason": "Bé!", "error_label": None}
            # Pas 1: free_text
            state = T.process_turn(state, "P(M1)=0.6, P(M2)=0.4, P(D|M1)=0.03, P(D|M2)=0.05")
            self.assertEqual(state["current_step_idx"], 1)
            # Pas 2: decimal — comparació determinista, NO crida a la IA
            state = T.process_turn(state, "0.038")
            self.assertEqual(state["current_step_idx"], 2)
            # Pas 3: fraction — comparació determinista
            state = T.process_turn(state, "9/19")
        self.assertEqual(state["verdict_final"], "solved")

    def test_decimal_step_no_llm_call(self):
        """El pas decimal NO ha de cridar `judge_step` si l'input parseja."""
        state = T.new_session_state("PROB-BAY-01", student_id="C1b")
        # Saltem el pas 1 manualment.
        state["current_step_idx"] = 1
        with patch("llm.judge_step") as mock_judge:
            # Si tot va bé, `judge_step` NO s'ha de cridar per a un decimal
            # parsejable, perquè la verificació és determinista.
            T.process_turn(state, "0.038")
            mock_judge.assert_not_called()

    def test_fraction_step_accepts_decimal_form(self):
        """9/19 ≈ 0.4737. L'engine ha d'acceptar les dues formes."""
        state = T.new_session_state("PROB-BAY-01", student_id="C1c")
        state["current_step_idx"] = 2  # saltem als passos 1-2
        with patch("llm.judge_step"):
            new_state = T.process_turn(state, "0.4737")
        self.assertEqual(new_state["verdict_final"], "solved")


class TestNumericFallbackToLLM(unittest.TestCase):
    """Si l'input del pas decimal/fraction no parseja, ha de delegar a la IA."""

    def test_unparseable_decimal_delegates_to_judge_step(self):
        state = T.new_session_state("PROB-BAY-01", student_id="FB")
        state["current_step_idx"] = 1  # pas decimal
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "typical_error",
                "reason": "Caldria un número, no raonament.",
                "error_label": "GEN_other",
            }
            new_state = T.process_turn(state, "no sé com calcular-ho")
            # Com que "no sé..." conté la paraula "calcul" (math keyword),
            # passa el filtre d'ús inadequat. Però no parseja com decimal,
            # així que delega a la IA.
            mock_judge.assert_called_once()
        self.assertEqual(new_state["current_step_idx"], 1)  # no avança


class TestTypicalErrorPath(unittest.TestCase):
    """Camí C2: error típic detectat per la IA, no retrocés a prerequisit."""

    def test_typical_error_stays_on_step(self):
        state = T.new_session_state("PROB-BAY-01", student_id="C2")
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "typical_error",
                "reason": "Has confós P(D|M1) amb P(M1|D).",
                "error_label": "BAY_invertit",
            }
            new_state = T.process_turn(state, "P(D|M1) = 0.6, perquè M1 produeix el 60 %")
        # Ha de quedar al pas 0
        self.assertEqual(new_state["current_step_idx"], 0)
        msgs = [m for m in new_state["messages"] if m["kind"] in ("feedback",)]
        self.assertTrue(len(msgs) > 0)


class TestConceptualGapPath(unittest.TestCase):
    """Camí C3: buit conceptual → retrocés a prerequisit."""

    def test_conceptual_gap_triggers_prereq(self):
        state = T.new_session_state("PROB-BAY-01", student_id="C3")
        with patch("llm.judge_step") as mock_judge, \
             patch("llm.diagnose_dependency") as mock_diag:
            mock_judge.return_value = {
                "verdict": "conceptual_gap",
                "reason": "No aplica la definició de probabilitat condicionada.",
                "error_label": "COND_invertit",
            }
            mock_diag.return_value = "def_prob_condicionada"
            new_state = T.process_turn(state, "no recordo què és P(D|M1)")

        # Ha d'haver activat un prerequisit
        self.assertIsNotNone(new_state["active_prereq"])
        self.assertEqual(new_state["active_prereq"], "PRE-COND")
        self.assertEqual(new_state["backtrack_depth"], 1)

    def test_prereq_correct_answer_closes_prereq(self):
        state = T.new_session_state("PROB-BAY-01", student_id="C3b")
        state["active_prereq"] = "PRE-COND"
        state["active_prereq_depth"] = 1
        state["backtrack_depth"] = 1
        # Resposta que conté "intersecció" i "/" (keywords requerides)
        new_state = T.process_turn(state, "P(A|B) és P(A intersecció B) / P(B)")
        self.assertIsNone(new_state["active_prereq"])
        resolved = [m for m in new_state["messages"] if m["kind"] == "prereq_resolved"]
        self.assertTrue(len(resolved) > 0)


class TestMaxBacktrack(unittest.TestCase):
    """MAX_BACKTRACK_DEPTH = 2: al límit, redirigeix al tutor."""

    def test_max_backtrack_triggers_referral(self):
        state = T.new_session_state("PROB-BAY-01", student_id="C4")
        state["backtrack_depth"] = T.MAX_BACKTRACK_DEPTH  # ja al límit
        with patch("llm.judge_step") as mock_judge, \
             patch("llm.diagnose_dependency") as mock_diag:
            mock_judge.return_value = {
                "verdict": "conceptual_gap",
                "reason": "Buit conceptual greu.",
                "error_label": None,
            }
            # Important: el dep_id mockejat NO ha de coincidir amb cap
            # paraula clau de la resposta de l'alumne, perquè altrament
            # `_quick_keyword_check` retornaria True i `_handle_conceptual_gap`
            # donaria pista en comptes d'intentar retrocés (i, per tant,
            # mai arribaria a la branca MAX_BACKTRACK_DEPTH).
            mock_diag.return_value = "def_prob_total"
            new_state = T.process_turn(
                state,
                "estic perdut sense pista, no sé com seguir el càlcul",
            )
        self.assertEqual(new_state["verdict_final"], "referred_to_tutor")


class TestInappropriateUse(unittest.TestCase):
    """Camí C5: ús inadequat (input sense contingut matemàtic).

    Portat de `tutor-eq/_handle_inappropriate`. Al 3r avís, suspended.
    """

    def test_non_math_input_warns(self):
        state = T.new_session_state("PROB-BAY-01", student_id="C5a")
        with patch("llm.judge_step") as mock_judge:
            new_state = T.process_turn(state, "hola què tal")
            # No s'ha cridat la IA — la detecció és pre-IA
            mock_judge.assert_not_called()
        self.assertEqual(new_state["inappropriate_warnings"], 1)
        self.assertIsNone(new_state["verdict_final"])
        warnings = [m for m in new_state["messages"] if m["kind"] == "warning"]
        self.assertTrue(len(warnings) > 0)

    def test_third_warning_suspends_session(self):
        state = T.new_session_state("PROB-BAY-01", student_id="C5b")
        with patch("llm.judge_step"):
            state = T.process_turn(state, "hola")
            state = T.process_turn(state, "com va això")
            state = T.process_turn(state, "ajuda")
        self.assertEqual(state["inappropriate_warnings"], 3)
        self.assertEqual(state["verdict_final"], "suspended")

    def test_math_content_resets_counter(self):
        state = T.new_session_state("PROB-BAY-01", student_id="C5c")
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {"verdict": "correct", "reason": "Bé", "error_label": None}
            state = T.process_turn(state, "hola")              # avís 1
            self.assertEqual(state["inappropriate_warnings"], 1)
            state = T.process_turn(state, "P(M1)=0.6, P(M2)=0.4, etc.")  # math → reset
            self.assertEqual(state["inappropriate_warnings"], 0)

    def test_math_keyword_passes_filter(self):
        """Un text curt amb una paraula clau matemàtica NO compta com a inadequat."""
        state = T.new_session_state("PROB-BAY-01", student_id="C5d")
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "conceptual_gap",
                "reason": "Falta context.",
                "error_label": None,
            }
            with patch("llm.diagnose_dependency", return_value="def_prob_condicionada"):
                state = T.process_turn(state, "bayes")  # única paraula, però és math keyword
        self.assertEqual(state["inappropriate_warnings"], 0)


class TestBuildTrace(unittest.TestCase):
    def test_trace_structure(self):
        state = T.new_session_state("PROB-BAY-01", student_id="trace_test")
        trace = T.build_trace(state)
        self.assertIn("session_id", trace)
        self.assertIn("problema", trace)
        self.assertIn("veredicte_final", trace)
        self.assertEqual(trace["veredicte_final"], "en_curs")
        # Camp nou:
        self.assertIn("avisos_us_inadequat", trace)
        self.assertEqual(trace["avisos_us_inadequat"], 0)


if __name__ == "__main__":
    unittest.main()
