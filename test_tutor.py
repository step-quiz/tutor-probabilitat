"""
Tests de la màquina d'estats (tutor.py).
No requereix Streamlit ni cridades reals a la IA (usa mocks).

Adaptació de `tutor-grups/tests/test_tutor.py` al domini de probabilitat:
- Problema canònic: `PROB-PAU-03` (3 passos: free_text → decimal → fraction).
- Tests addicionals per al verdict `suspended` (ús inadequat).
"""

import unittest
from unittest.mock import patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tutor as T
import problems as PB  # noqa: F401 — importat per coherència amb test_tutor de tutor-grups
import llm as L


class TestNewSessionState(unittest.TestCase):
    def test_initial_state_pau03(self):
        state = T.new_session_state("PROB-PAU-03")
        self.assertEqual(state["problem_id"], "PROB-PAU-03")
        self.assertEqual(state["current_step_idx"], 0)
        self.assertIsNone(state["verdict_final"])
        self.assertIsNone(state["active_prereq"])
        self.assertEqual(state["backtrack_depth"], 0)
        # Sessió anònima: cap identificador d'alumne.
        self.assertIsNone(state["student_id"])
        # Camp nou portat de tutor-eq:
        self.assertEqual(state["inappropriate_warnings"], 0)

    def test_initial_state_has_3_steps(self):
        state = T.new_session_state("PROB-PAU-03")
        self.assertEqual(len(state["problem"]["passos"]), 3)


class TestCorrectPath(unittest.TestCase):
    """Camí C1: l'alumne respon correctament tots els passos de PROB-PAU-03."""

    def test_correct_answers_advance_step(self):
        state = T.new_session_state("PROB-PAU-03")
        # Pas 1 = free_text → IA. Pas 2 = decimal (0.0615) → determinista.
        # Pas 3 = fraction ("30/41") → determinista.
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {"verdict": "correct", "reason": "Bé!", "error_label": None}
            # Pas 1: free_text
            state = T.process_turn(state, "P(I)=0.45, P(Ī)=0.55, P(S|I)=0.10, P(S|Ī)=0.03")
            self.assertEqual(state["current_step_idx"], 1)
            # Pas 2: decimal — comparació determinista, NO crida a la IA
            state = T.process_turn(state, "0.0615")
            self.assertEqual(state["current_step_idx"], 2)
            # Pas 3: fraction — comparació determinista
            state = T.process_turn(state, "30/41")
        self.assertEqual(state["verdict_final"], "solved")

    def test_decimal_step_no_llm_call(self):
        """El pas decimal NO ha de cridar `judge_step` si l'input parseja."""
        state = T.new_session_state("PROB-PAU-03")
        # Saltem el pas 1 manualment.
        state["current_step_idx"] = 1
        with patch("llm.judge_step") as mock_judge:
            # Si tot va bé, `judge_step` NO s'ha de cridar per a un decimal
            # parsejable, perquè la verificació és determinista.
            T.process_turn(state, "0.0615")
            mock_judge.assert_not_called()

    def test_fraction_step_accepts_decimal_form(self):
        """9/19 ≈ 0.4737. L'engine ha d'acceptar les dues formes."""
        state = T.new_session_state("PROB-PAU-03")
        state["current_step_idx"] = 2  # saltem als passos 1-2
        with patch("llm.judge_step"):
            new_state = T.process_turn(state, "0.7317")
        self.assertEqual(new_state["verdict_final"], "solved")


class TestNumericFallbackToLLM(unittest.TestCase):
    """Si l'input del pas decimal/fraction no parseja, ha de delegar a la IA."""

    def test_unparseable_decimal_delegates_to_judge_step(self):
        state = T.new_session_state("PROB-PAU-03")
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
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "typical_error",
                "reason": "Has confós P(S|I) amb P(I|S).",
                "error_label": "BAY_invertit",
            }
            new_state = T.process_turn(state, "P(I|S) = 0.45, perquè el 45 % practiquen esports d'impacte")
        # Ha de quedar al pas 0
        self.assertEqual(new_state["current_step_idx"], 0)
        msgs = [m for m in new_state["messages"] if m["kind"] in ("feedback",)]
        self.assertTrue(len(msgs) > 0)


class TestConceptualGapPath(unittest.TestCase):
    """Camí C3: buit conceptual → retrocés a prerequisit."""

    def test_conceptual_gap_triggers_prereq(self):
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm.judge_step") as mock_judge, \
             patch("llm.diagnose_dependency") as mock_diag:
            mock_judge.return_value = {
                "verdict": "conceptual_gap",
                "reason": "No aplica la definició de probabilitat condicionada.",
                "error_label": "COND_invertit",
            }
            mock_diag.return_value = "def_prob_condicionada"
            new_state = T.process_turn(state, "no recordo què és P(S|I)")

        # Ha d'haver activat un prerequisit
        self.assertIsNotNone(new_state["active_prereq"])
        self.assertEqual(new_state["active_prereq"], "PRE-COND")
        self.assertEqual(new_state["backtrack_depth"], 1)

    def test_prereq_correct_answer_closes_prereq(self):
        state = T.new_session_state("PROB-PAU-03")
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
        state = T.new_session_state("PROB-PAU-03")
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
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm.judge_step") as mock_judge:
            new_state = T.process_turn(state, "hola què tal")
            # No s'ha cridat la IA — la detecció és pre-IA
            mock_judge.assert_not_called()
        self.assertEqual(new_state["inappropriate_warnings"], 1)
        self.assertIsNone(new_state["verdict_final"])
        warnings = [m for m in new_state["messages"] if m["kind"] == "warning"]
        self.assertTrue(len(warnings) > 0)

    def test_third_warning_suspends_session(self):
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm.judge_step"):
            state = T.process_turn(state, "hola")
            state = T.process_turn(state, "com va això")
            state = T.process_turn(state, "ajuda")
        self.assertEqual(state["inappropriate_warnings"], 3)
        self.assertEqual(state["verdict_final"], "suspended")

    def test_math_content_resets_counter(self):
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {"verdict": "correct", "reason": "Bé", "error_label": None}
            state = T.process_turn(state, "hola")              # avís 1
            self.assertEqual(state["inappropriate_warnings"], 1)
            state = T.process_turn(state, "P(I)=0.45, P(Ī)=0.55, etc.")  # math → reset
            self.assertEqual(state["inappropriate_warnings"], 0)

    def test_math_keyword_passes_filter(self):
        """Un text curt amb una paraula clau matemàtica NO compta com a inadequat."""
        state = T.new_session_state("PROB-PAU-03")
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
        state = T.new_session_state("PROB-PAU-03")
        trace = T.build_trace(state)
        self.assertIn("session_id", trace)
        self.assertIn("problema", trace)
        self.assertIn("veredicte_final", trace)
        self.assertEqual(trace["veredicte_final"], "en_curs")
        # Camp nou:
        self.assertIn("avisos_us_inadequat", trace)
        self.assertEqual(trace["avisos_us_inadequat"], 0)
        # Capa 1: comptador de re-preguntes socràtiques per resposta parcial.
        self.assertIn("incomplete_followups", trace)
        self.assertEqual(trace["incomplete_followups"], 0)


# ============================================================
# Capa 1: veredicte `incomplete`
# ============================================================
# Reprodueix el cas detectat al log `1b7c646b` (2026-05-14): l'alumne
# respon correctament al primer pas de `PROB-PAU-03` però només
# identifica 2 de les 4 probabilitats. La IA havia de marcar
# `conceptual_gap`; després de Capa 1 ha de marcar `incomplete`.
class TestIncompleteVerdict(unittest.TestCase):
    """Veredicte `incomplete`: resposta parcialment correcta, no avança el pas."""

    def test_incomplete_does_not_advance_step(self):
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "incomplete",
                "reason": "Has identificat bé I, S, P(I) i P(S|I).",
                "error_label": None,
                "missing": "P(Ī) i P(S|Ī).",
                "next_question": "Quina és la probabilitat complementària de P(I)?",
            }
            new_state = T.process_turn(
                state, "I=esports d'impacte, S=sesamoïditis. P(I)=0,45 P(S|I)=0,1"
            )
        # No s'ha avançat de pas.
        self.assertEqual(new_state["current_step_idx"], 0)
        # No s'ha marcat veredicte final.
        self.assertIsNone(new_state["verdict_final"])

    def test_incomplete_appends_to_step_partials(self):
        state = T.new_session_state("PROB-PAU-03")
        self.assertEqual(state["step_partials"], [])
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "incomplete",
                "reason": "Has identificat dues probabilitats.",
                "error_label": None,
                "missing": "Falten les complementàries.",
                "next_question": "Què val P(Ī)?",
            }
            partial1 = "P(I)=0,45 P(S|I)=0,1"
            new_state = T.process_turn(state, partial1)
        self.assertEqual(new_state["step_partials"], [partial1])

    def test_incomplete_does_not_trigger_prereq(self):
        """`incomplete` no ha d'activar `_handle_conceptual_gap`."""
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm.judge_step") as mock_judge, \
             patch("llm.diagnose_dependency") as mock_diag:
            mock_judge.return_value = {
                "verdict": "incomplete",
                "reason": "Bé fins aquí.",
                "error_label": None,
                "missing": "Falta P(Ī).",
                "next_question": "Calcula la complementària.",
            }
            new_state = T.process_turn(state, "P(I)=0,45")
            # Crucial: `diagnose_dependency` NO s'ha de cridar.
            mock_diag.assert_not_called()
        self.assertIsNone(new_state["active_prereq"])
        self.assertEqual(new_state["backtrack_depth"], 0)
        self.assertEqual(new_state["backtrack_count"], 0)

    def test_incomplete_does_not_increment_stagnation(self):
        """Una conversa socràtica de diversos torns sobre el mateix pas és sana."""
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "incomplete",
                "reason": "Bé.",
                "error_label": None,
                "missing": "Falten coses.",
                "next_question": "Continua.",
            }
            new_state = T.process_turn(state, "P(I)=0,45")
            new_state = T.process_turn(new_state, "P(S|I)=0,1")
            new_state = T.process_turn(new_state, "P(Ī)=0,55")
        # Tres torns d'`incomplete` no han de comptar com a estancament.
        self.assertEqual(new_state["stagnation_consecutive"], 0)
        # Però SÍ s'han acumulat com a parcials.
        self.assertEqual(len(new_state["step_partials"]), 3)

    def test_incomplete_does_not_touch_concept_failure_streak(self):
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "incomplete",
                "reason": "Bé.",
                "error_label": None,
                "missing": "X",
                "next_question": "Y?",
            }
            new_state = T.process_turn(state, "P(I)=0,45")
        self.assertEqual(new_state["concept_failure_streak"], {})

    def test_judge_step_receives_step_partials(self):
        """A partir del 2n torn `incomplete`, el judge ha de rebre el rerefons."""
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "incomplete",
                "reason": "Bé.",
                "error_label": None,
                "missing": "Falten 2.",
                "next_question": "I la complementària?",
            }
            T.process_turn(state, "P(I)=0,45")
            # La 1a crida no ha de portar step_partials (encara no n'hi havia).
            args1, kwargs1 = mock_judge.call_args
            # Cridem via positional: (step, student_answer, partials)
            partials_arg_1 = args1[2] if len(args1) >= 3 else kwargs1.get("step_partials")
            self.assertIn(partials_arg_1, (None, []))

        # 2a crida: ja hi ha un parcial gravat.
        state = T.new_session_state("PROB-PAU-03")
        state["step_partials"] = ["P(I)=0,45"]
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "incomplete",
                "reason": "Bé.",
                "error_label": None,
                "missing": "Una més.",
                "next_question": "I P(Ī)?",
            }
            T.process_turn(state, "P(S|I)=0,1")
            args, kwargs = mock_judge.call_args
            partials_arg = args[2] if len(args) >= 3 else kwargs.get("step_partials")
            self.assertEqual(partials_arg, ["P(I)=0,45"])

    def test_cumulative_correct_advances_and_resets_partials(self):
        """Diverses parcials + una correcta cumulativa: avança i reseteja."""
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm.judge_step") as mock_judge:
            # Primer torn: incomplete.
            mock_judge.return_value = {
                "verdict": "incomplete",
                "reason": "Has trobat I, S, P(I), P(S|I).",
                "error_label": None,
                "missing": "P(Ī) i P(S|Ī).",
                "next_question": "Quines són les complementàries?",
            }
            state = T.process_turn(state, "P(I)=0,45 P(S|I)=0,1")
            self.assertEqual(state["current_step_idx"], 0)
            self.assertEqual(state["step_partials"], ["P(I)=0,45 P(S|I)=0,1"])

            # Segon torn: la unió ja cobreix l'esperat → correct.
            mock_judge.return_value = {
                "verdict": "correct",
                "reason": "Ara sí, les quatre probabilitats.",
                "error_label": None,
                "missing": None,
                "next_question": None,
            }
            state = T.process_turn(state, "P(Ī)=0,55 P(S|Ī)=0,03")

        # Hem avançat al pas 1 i les parcials s'han resetat.
        self.assertEqual(state["current_step_idx"], 1)
        self.assertEqual(state["step_partials"], [])

    def test_incomplete_records_missing_and_next_question_in_history(self):
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "incomplete",
                "reason": "Has trobat 2 de 4.",
                "error_label": None,
                "missing": "P(Ī) i P(S|Ī).",
                "next_question": "Comença per la complementària de P(I).",
            }
            new_state = T.process_turn(state, "P(I)=0,45 P(S|I)=0,1")
        last = new_state["history"][-1]
        self.assertEqual(last["verdict"], "incomplete")
        self.assertEqual(last["missing"], "P(Ī) i P(S|Ī).")
        self.assertEqual(last["next_question"], "Comença per la complementària de P(I).")

    def test_typical_error_does_not_reset_partials(self):
        """Una errada típica enmig de parcials no esborra les parcials prèvies."""
        state = T.new_session_state("PROB-PAU-03")
        state["step_partials"] = ["P(I)=0,45"]
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "typical_error",
                "reason": "P(I|S) no és P(S|I).",
                "error_label": "BAY_invertit",
            }
            new_state = T.process_turn(state, "P(I|S)=0,1, és el mateix")
        # Parcials anteriors es conserven; l'alumne pot tornar a aportar.
        self.assertEqual(new_state["step_partials"], ["P(I)=0,45"])

    def test_invalid_verdict_defaults_to_typical_error_not_incomplete(self):
        """Defensiu: un veredicte malformat NO ha de defaulteggar a `incomplete`.

        Si el model ens torna brossa, volem marcar typical_error (que mostra
        feedback i l'alumne pot reintentar), no incomplete (que no avança
        i podria induir bucles). Aquesta protecció és a `llm.judge_step`
        però la verifiquem aquí end-to-end.
        """
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm._call_json") as mock_call:
            mock_call.return_value = '{"verdict": "wat_is_this", "reason": "?"}'
            new_state = T.process_turn(state, "P(I)=0,45 P(S|I)=0,1")
        last = new_state["history"][-1]
        self.assertEqual(last["verdict"], "typical_error")
        # I no ha tocat step_partials.
        self.assertEqual(new_state["step_partials"], [])

    def test_incomplete_pushes_feedback_message(self):
        """L'alumne ha de veure reconeixement + re-pregunta."""
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "incomplete",
                "reason": "Has identificat I, S, P(I), P(S|I).",
                "error_label": None,
                "missing": "P(Ī) i P(S|Ī).",
                "next_question": "Quina és la probabilitat de no practicar esports d'impacte?",
            }
            new_state = T.process_turn(state, "P(I)=0,45 P(S|I)=0,1")
        feedbacks = [m for m in new_state["messages"] if m["kind"] == "feedback"]
        self.assertEqual(len(feedbacks), 1)
        txt = feedbacks[0]["text"]
        # Han de aparèixer tant el reconeixement com la re-pregunta.
        self.assertIn("I, S, P(I), P(S|I)", txt)
        self.assertIn("no practicar", txt)

    def test_trace_counts_incomplete_followups(self):
        state = T.new_session_state("PROB-PAU-03")
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "incomplete",
                "reason": "Vas bé.",
                "error_label": None,
                "missing": "Una cosa més.",
                "next_question": "Continua.",
            }
            state = T.process_turn(state, "P(I)=0,45")
            state = T.process_turn(state, "P(S|I)=0,1")
        trace = T.build_trace(state)
        self.assertEqual(trace["incomplete_followups"], 2)


# ============================================================
# Capa B: run_exhaustive_test
# ============================================================
# Tests amb `llm.judge_step` mockejat. NO criden la IA real. Validen la
# infraestructura del runner (extracció de camps del state, càlcul del
# match, captura d'excepcions, avanç de baseline, etc.). El test
# "exhaustiu real" (cost API) NO és part d'aquesta suite — s'executa
# manualment des del botó del mode debug.
class TestExhaustiveTestRunner(unittest.TestCase):
    """`tutor.run_exhaustive_test` — runner del test exhaustiu."""

    def test_empty_test_cases_returns_empty_list(self):
        """Un problema sense TEST_CASES retorna [] sense crides."""
        # PROB-PAU-01 té entrada buida `[]` al schema → get_test_cases
        # ha de retornar None i el runner ha de sortir aviat.
        with patch("llm.judge_step") as mock_judge:
            results = T.run_exhaustive_test("PROB-PAU-01")
        self.assertEqual(results, [])
        mock_judge.assert_not_called()

    def test_unknown_problem_id_returns_empty_list(self):
        results = T.run_exhaustive_test("PROB-INEXISTENT")
        self.assertEqual(results, [])

    def test_runner_executes_all_rounds_of_pau03(self):
        """3 rondes × 4 inputs = 12 items per a PAU-03 (un problema real)."""
        # Mockeggem judge_step amb una resposta `correct` per defecte
        # (els passos deterministes no toquen la IA; al pas 1 free_text
        # tots 4 inputs van a la IA mock).
        def fake_judge(step, student, partials=None):
            # Per al primer input "definitivament correcte" retornem correct;
            # per als altres, typical_error. Detectem pel contingut.
            if "P(Ī)" in student and "P(S|Ī)" in student:
                return {"verdict": "correct", "reason": "OK",
                        "error_label": None, "missing": None,
                        "next_question": None}
            if "P(I)=0,45 P(S|I)=0,1" in student or "P(I)=0.45 P(S|I)=0.1" in student:
                return {"verdict": "incomplete", "reason": "Falten 2.",
                        "error_label": None, "missing": "P(Ī).",
                        "next_question": "I?"}
            return {"verdict": "typical_error", "reason": "Mal.",
                    "error_label": "COND_invertit", "missing": None,
                    "next_question": None}

        with patch("llm.judge_step", side_effect=fake_judge):
            results = T.run_exhaustive_test("PROB-PAU-03")

        # 3 rondes (una per pas).
        self.assertEqual(len(results), 3)
        # Cada ronda té 4 items.
        for r in results:
            self.assertEqual(len(r["items"]), 4)

    def test_match_logic_verdict_only(self):
        """Match=True quan el verdict coincideix amb l'expected."""
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "incomplete",
                "reason": "Falten 2.",
                "error_label": None,
                "missing": "P(Ī).",
                "next_question": "I?",
            }
            # PAU-03 ronda 1 item 2 té expected="incomplete".
            results = T.run_exhaustive_test("PROB-PAU-03")
        round1 = results[0]
        item_incomplete = round1["items"][1]  # el cas del log 1b7c646b
        self.assertEqual(item_incomplete["expected"], "incomplete")
        self.assertEqual(item_incomplete["verdict"], "incomplete")
        self.assertTrue(item_incomplete["match"])

    def test_match_logic_with_expected_error_label(self):
        """Quan expected_error_label és present, també ha de coincidir."""
        # Forçem que la IA assigni un label diferent del que espera el guió.
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "typical_error",
                "reason": "Mal.",
                "error_label": "GEN_other",  # diferent del COND_invertit esperat
                "missing": None,
                "next_question": None,
            }
            results = T.run_exhaustive_test("PROB-PAU-03")
        # Ronda 1 item 3: expected="typical_error",
        # expected_error_label="COND_invertit". Verdict OK però label NO.
        item = results[0]["items"][2]
        self.assertEqual(item["expected"], "typical_error")
        self.assertEqual(item["expected_error_label"], "COND_invertit")
        self.assertEqual(item["verdict"], "typical_error")
        self.assertEqual(item["error_label"], "GEN_other")
        self.assertFalse(item["match"])  # label diferent → no match

    def test_exception_swallowed_by_process_turn_surfaces_as_warning(self):
        """`process_turn` ja captura les excepcions de `judge_step` i les
        converteix en un missatge `warning` al state. El runner veu un
        verdict=None sense un `exception` propagat. Aquest test documenta
        aquest contracte (per si mai canvia)."""
        with patch("llm.judge_step",
                   side_effect=RuntimeError("simulated API timeout")):
            results = T.run_exhaustive_test("PROB-PAU-03")
        # Pas 1: tots els ítems han d'haver fallat sense excepció propagada
        # i sense step entry al history (verdict=None).
        pas1_items = results[0]["items"]
        for it in pas1_items:
            self.assertIsNone(it["exception"],
                              "process_turn captura, el runner no ha de veure exception")
            self.assertIsNone(it["verdict"])
            self.assertFalse(it["match"])
        # El baseline NO ha avançat (no hi ha hagut "correct"); ha de
        # produir l'entrada d'interrupció.
        self.assertEqual(len(results), 2)
        interrupt = results[1]
        self.assertEqual(interrupt["items"], [])
        self.assertIn("schema_warning", interrupt)

    def test_exception_outside_judge_does_propagate_to_item(self):
        """Si `process_turn` peta per una excepció NO capturada (per
        exemple, bug intern al copy.deepcopy), llavors el runner sí que
        atrapa l'excepció al camp `exception`."""
        # Mockejar copy.deepcopy de tutor per fer-lo petar dins process_turn.
        original_deepcopy = T.copy.deepcopy
        call_count = [0]
        def flaky_deepcopy(x):
            call_count[0] += 1
            # La primera crida ve del runner (copia el baseline). Deixem-la
            # passar. La segona ve de process_turn (copia el seu state).
            # Aquesta la fem petar.
            if call_count[0] == 2:
                raise RuntimeError("simulated internal bug")
            return original_deepcopy(x)

        with patch("llm.judge_step") as mock_judge, \
             patch.object(T.copy, "deepcopy", side_effect=flaky_deepcopy):
            mock_judge.return_value = {
                "verdict": "correct", "reason": "OK", "error_label": None,
                "missing": None, "next_question": None,
            }
            results = T.run_exhaustive_test("PROB-PAU-03")
        # El primer item de la ronda 1 ha de tenir exception.
        first_item = results[0]["items"][0]
        self.assertIsNotNone(first_item["exception"])
        self.assertIn("simulated internal bug", first_item["exception"])

    def test_baseline_advances_after_first_correct(self):
        """El baseline ha de passar al pas següent després d'una correct."""
        # Mockeggem judge_step amb tot correct (només cal per al pas 1
        # free_text). Els passos 2/3 deterministes acceptaran les
        # respostes correctes dels TEST_CASES.
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "correct",
                "reason": "OK",
                "error_label": None,
                "missing": None,
                "next_question": None,
            }
            results = T.run_exhaustive_test("PROB-PAU-03")
        # Una entrada per ronda + cap entrada d'interrupció.
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["from_step_idx"], 0)
        self.assertEqual(results[1]["from_step_idx"], 1)
        self.assertEqual(results[2]["from_step_idx"], 2)

    def test_runner_isolates_log_context(self):
        """El runner reescriu el log_context i el restaura després."""
        # Set un context "alumne real".
        L.set_log_context(student_id="alumne_X", session_id="sessio_real")
        try:
            with patch("llm.judge_step") as mock_judge:
                mock_judge.return_value = {
                    "verdict": "correct", "reason": "OK", "error_label": None,
                    "missing": None, "next_question": None,
                }
                T.run_exhaustive_test("PROB-PAU-03", session_id="test_abc")
            # Després del test, ha de tornar al context original.
            stu, sid = L.get_log_context()
            self.assertEqual(stu, "alumne_X")
            self.assertEqual(sid, "sessio_real")
        finally:
            L.set_log_context(student_id=None, session_id=None)

    def test_progress_callback_invoked_correctly(self):
        """on_progress es crida amb (round_idx, n_rounds, item_idx, n_items)."""
        calls = []
        def on_p(r, nr, i, ni):
            calls.append((r, nr, i, ni))
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "correct", "reason": "OK", "error_label": None,
                "missing": None, "next_question": None,
            }
            T.run_exhaustive_test("PROB-PAU-03", on_progress=on_p)
        # 3 rondes × 4 items = 12 crides al callback.
        self.assertEqual(len(calls), 12)
        # Primer call: ronda 1, 3 rondes, item 1, 4 items.
        self.assertEqual(calls[0], (1, 3, 1, 4))
        # Última crida: ronda 3, 3 rondes, item 4, 4 items.
        self.assertEqual(calls[-1], (3, 3, 4, 4))

    def test_progress_callback_exception_does_not_break(self):
        """Si on_progress peta, el runner ha de continuar."""
        def bad_callback(*args):
            raise RuntimeError("UI crashed")
        with patch("llm.judge_step") as mock_judge:
            mock_judge.return_value = {
                "verdict": "correct", "reason": "OK", "error_label": None,
                "missing": None, "next_question": None,
            }
            # No ha de propagar l'excepció.
            results = T.run_exhaustive_test("PROB-PAU-03", on_progress=bad_callback)
        self.assertEqual(len(results), 3)

    def test_log_context_remains_test_sid_during_run(self):
        """REGRESSIÓ: `new_session_state` crida internament `set_log_context`
        amb un session_id NOU. Si el runner no el restaura després, totes
        les crides reals a la IA quedaven loguejades sota aquest uuid
        aleatori en lloc del `test_sid`, i `summarize_session(test_sid)`
        tornava 0 crides (cost=$0.00) tot i que el test havia gastat API.

        Aquest test verifica que durant la crida a `judge_step`, el
        context del thread és el `test_sid` que volem (no l'uuid del
        baseline que `new_session_state` ha creat).
        """
        captured_sids = []

        def capturing_judge(step, student, partials=None):
            # Capturem el session_id ACTIU al moment de la crida.
            _, current_sid = L.get_log_context()
            captured_sids.append(current_sid)
            return {
                "verdict": "correct", "reason": "OK", "error_label": None,
                "missing": None, "next_question": None,
            }

        L.set_log_context(student_id=None, session_id=None)  # reset
        try:
            with patch("llm.judge_step", side_effect=capturing_judge):
                T.run_exhaustive_test("PROB-PAU-03", session_id="test_xyz_123")
        finally:
            L.set_log_context(student_id=None, session_id=None)

        # PAU-03 té 1 pas free_text al pas 1 amb 4 inputs → 4 crides al
        # mock. Els passos 2 i 3 són determinístics i NO criden judge_step
        # per als inputs correctes. Tampoc per a inputs numèrics ben
        # formats (0.045, etc.). Així que el comptador esperat és 4
        # (els 4 inputs del pas 1 + avanç de baseline al final del pas 1).
        # OJO: el primer input es processa DOS vegades — una per al match
        # report i una per avançar el baseline. Per tant 4 + 1 = 5.
        # Però només cal verificar que TOTS els sids capturats són
        # `test_xyz_123` (no que en siguin 4 o 5 exactes).
        self.assertGreater(len(captured_sids), 0,
                           "judge_step mai s'ha cridat (el test no ha provat res)")
        for sid in captured_sids:
            self.assertEqual(sid, "test_xyz_123",
                             f"Context perdut: {sid!r} en lloc de 'test_xyz_123'")


if __name__ == "__main__":
    unittest.main()
