"""
Tests d'integració: cada problema PAU del `PILOT_PATH` ha de ser
atravessable amb respostes correctes a tots els passos.

No es fa cap crida real a la IA:
- Passos `free_text` → `L.judge_step` mockejat perquè retorni `correct`.
- Passos numèrics (`integer`, `decimal`, `fraction`, `set_listing`)
  → s'envia el valor exacte de `step["expected_value"]` i el
  verificador determinista (`_check_integer`/`_check_numeric`/
  `_check_set` a `tutor.py`) el valida sense IA.

Aquesta família de tests captura regressions com:
- Decimals que cauen fora de la tolerància 1e-4 (p.ex. expected_value
  arrodonit massa fort).
- IDs al `PILOT_PATH` que ja no existeixen a `PROBLEMS`.
- Passos amb `input_type` no suportat per `process_turn`.
- Passos deterministes que es perden l'`expected_value`.
- Estructures de `PROBLEMS` malformades que farien fallar l'engine
  durant una sessió real.

També inclou una asserció important sobre el comptatge de crides a
la IA: ha de ser exactament igual al nombre de passos `free_text`
del problema. Si un pas determinista comencés a delegar a la IA per
error, el test fallaria.
"""

import unittest
from unittest.mock import patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tutor as T
import problems as PB


# ============================================================
# Helpers
# ============================================================
def _answer_for_step(step: dict) -> str:
    """
    Genera una resposta «correcta» per a un pas, segons `input_type`.

    Per als tipus deterministes, formatejem `expected_value` com a
    string i confiem en `_check_*` per validar-lo. Per a `free_text`,
    enviem una resposta neutra però amb contingut matemàtic mínim
    (dígits + paraula clau) per passar el filtre `_has_math_content`
    abans del despatx a `judge_step`.

    Notes per `input_type`:
    - `integer`     → `str(int(expected_value))`
    - `decimal`     → `str(expected_value)` (Python imprimeix p.ex.
      `0.042` sense canviar la precisió)
    - `fraction`    → `str(expected_value)` funciona tant si és str
      ("30/41") com float (0.7317); `_check_numeric` parseja tots dos
    - `set_listing` → join amb coma; `_check_set` accepta espais,
      claus i case-insensitive
    - `free_text`   → string amb dígits perquè `_has_math_content` el
      consideri matemàtic; el contingut concret no importa perquè la
      IA està mockejada
    """
    it = step.get("input_type", "free_text")
    ev = step.get("expected_value")
    if it == "integer":
        return str(int(ev))
    if it == "decimal":
        return str(ev)
    if it == "fraction":
        # ev pot ser un str ("30/41") o un float (0.7317). En tots dos
        # casos, str() retorna la forma adequada per a Fraction().
        return str(ev)
    if it == "set_listing":
        # Llista d'strings → "a, b, c". `_check_set` accepta separadors
        # variats i és case-insensitive.
        return ", ".join(str(x) for x in ev)
    # free_text: cal contingut matemàtic. Un decimal qualsevol passa
    # `_has_math_content` (té dígits).
    return "Resposta correcta amb dades del problema: P(X)=0.5"


# ============================================================
# Tests d'atravessament: un per problema (test discovery els llista
# individualment, així si un problema concret falla saps quin)
# ============================================================
class TestPauProblemsTraversable(unittest.TestCase):
    """Per a cada problema del PILOT_PATH, simulem una sessió on totes
    les respostes són les correctes i verifiquem que arriba a `solved`."""

    def _traverse(self, problem_id: str):
        """Reprodueix una sessió completa. Reutilitzat pels 6 tests."""
        state = T.new_session_state(problem_id)
        problem = state["problem"]
        steps = problem["passos"]
        n_free_text = sum(1 for s in steps if s["input_type"] == "free_text")

        correct_judgment = {
            "verdict": "correct",
            "reason": "Bé.",
            "error_label": None,
        }

        # Patch global a `llm.judge_step` per a tota la durada de la
        # sessió. Els passos deterministes NO arribaran al mock (el
        # despatx per input_type els resol abans); els free_text sí.
        with patch("llm.judge_step", return_value=correct_judgment) as mock_judge:
            for i, step in enumerate(steps):
                answer = _answer_for_step(step)
                state = T.process_turn(state, answer)

                # Després de cada pas correcte, current_step_idx avança
                # exactament en 1. Al darrer pas, també queda a i+1
                # (és a dir, == len(steps)), i `_maybe_finish` posa
                # verdict_final = "solved" en aquell mateix torn.
                self.assertEqual(
                    state["current_step_idx"], i + 1,
                    f"{problem_id} pas {step['id']} ({step['input_type']}): "
                    f"esperava avançar a idx={i+1}, però ha quedat a "
                    f"idx={state['current_step_idx']}. "
                    f"Resposta enviada: {answer!r}. "
                    f"Veredicte de l'últim torn al history: "
                    f"{state['history'][-1] if state['history'] else None}",
                )

        # Assercions de tancament: tot el problema s'ha completat amb
        # èxit i el comptatge de crides a la IA quadra.
        self.assertEqual(
            state["verdict_final"], "solved",
            f"{problem_id}: no s'ha marcat com a 'solved'. "
            f"verdict_final={state['verdict_final']}, "
            f"idx={state['current_step_idx']}/{len(steps)}",
        )
        self.assertEqual(
            mock_judge.call_count, n_free_text,
            f"{problem_id}: esperava {n_free_text} crides a judge_step "
            f"(una per cada pas free_text), però se n'han fet "
            f"{mock_judge.call_count}. "
            f"Això suggereix que algun pas determinista està delegant "
            f"incorrectament a la IA.",
        )

    def test_pau_01_traversable(self):
        """PROB-PAU-01: ferro/acer + demostració binomial (3 passos)."""
        self._traverse("PROB-PAU-01")

    def test_pau_02_traversable(self):
        """PROB-PAU-02: filtre spam, total + Bayes (3 passos)."""
        self._traverse("PROB-PAU-02")

    def test_pau_03_traversable(self):
        """PROB-PAU-03: sesamoïditis, total + Bayes (3 passos)."""
        self._traverse("PROB-PAU-03")

    def test_pau_04_traversable(self):
        """PROB-PAU-04: BAYES FANS, Laplace + binomial + complement (4 passos)."""
        self._traverse("PROB-PAU-04")

    def test_pau_05_traversable(self):
        """PROB-PAU-05: Rut deures, total + Bayes + binomial (4 passos)."""
        self._traverse("PROB-PAU-05")

    def test_pau_06_traversable(self):
        """PROB-PAU-06: Holter, binomial + total + Bayes amb H̄ (4 passos)."""
        self._traverse("PROB-PAU-06")


# ============================================================
# Tests estructurals sobre el PILOT_PATH (no atravessen sessions,
# només validen forma i convencions)
# ============================================================
class TestPilotPathStructure(unittest.TestCase):
    """Garanties estàtiques sobre l'estructura de `PILOT_PATH` i els
    problemes que conté. Es resolen sense aixecar cap sessió, així que
    són ràpides i diagnostiquen errors de configuració."""

    def test_all_pilot_problems_exist_in_problems_dict(self):
        for pid in PB.PILOT_PATH:
            self.assertIn(
                pid, PB.PROBLEMS,
                f"{pid} apareix al PILOT_PATH però no s'ha definit a PROBLEMS",
            )

    def test_all_pilot_problems_have_at_least_one_step(self):
        for pid in PB.PILOT_PATH:
            p = PB.PROBLEMS[pid]
            self.assertGreater(
                len(p["passos"]), 0,
                f"{pid}: la llista `passos` està buida — la sessió no "
                f"avançaria mai",
            )

    def test_all_steps_have_valid_input_type(self):
        """Tot pas ha de tenir un `input_type` reconegut per `process_turn`.
        Si afegim un input_type nou al schema, cal estendre l'engine alhora."""
        valid_types = {"free_text", "integer", "decimal", "fraction", "set_listing"}
        for pid in PB.PILOT_PATH:
            p = PB.PROBLEMS[pid]
            for s in p["passos"]:
                self.assertIn(
                    s["input_type"], valid_types,
                    f"{pid}.{s['id']}: input_type {s['input_type']!r} no és "
                    f"un dels acceptats {valid_types}",
                )

    def test_deterministic_steps_have_expected_value(self):
        """Tot pas amb input_type determinista (no free_text) ha de tenir
        `expected_value` no-nul. Sense això, el verificador retornaria
        `None` i delegaria a la IA, que no és el que volem."""
        deterministic = {"integer", "decimal", "fraction", "set_listing"}
        for pid in PB.PILOT_PATH:
            p = PB.PROBLEMS[pid]
            for s in p["passos"]:
                if s["input_type"] in deterministic:
                    self.assertIsNotNone(
                        s.get("expected_value"),
                        f"{pid}.{s['id']}: input_type {s['input_type']!r} "
                        f"hauria de tenir `expected_value` definit (no None)",
                    )

    def test_all_step_dependencies_resolve(self):
        """Tota dependència citada (a `dependencies` del problema o a
        `key_concepts` d'un pas) ha d'existir a DEPENDENCIES. Si no, el
        diagnostic_dependency podria retornar un dep_id invàlid."""
        for pid in PB.PILOT_PATH:
            p = PB.PROBLEMS[pid]
            for dep in p.get("dependencies", []):
                self.assertIn(
                    dep, PB.DEPENDENCIES,
                    f"{pid}: dependencia {dep!r} no existeix a DEPENDENCIES",
                )
            for s in p["passos"]:
                for k in s.get("key_concepts", []):
                    self.assertIn(
                        k, PB.DEPENDENCIES,
                        f"{pid}.{s['id']}: key_concept {k!r} no existeix "
                        f"a DEPENDENCIES",
                    )

    def test_all_typical_error_labels_resolve(self):
        """Tota etiqueta d'error citada (a `errors_freqüents` del problema
        o a `typical_error_label` d'un pas) ha d'existir a ERROR_CATALOG."""
        for pid in PB.PILOT_PATH:
            p = PB.PROBLEMS[pid]
            for err in p.get("errors_freqüents", []):
                self.assertIn(
                    err, PB.ERROR_CATALOG,
                    f"{pid}: etiqueta d'error {err!r} no existeix a "
                    f"ERROR_CATALOG",
                )
            for s in p["passos"]:
                lab = s.get("typical_error_label")
                if lab:
                    self.assertIn(
                        lab, PB.ERROR_CATALOG,
                        f"{pid}.{s['id']}: typical_error_label {lab!r} "
                        f"no existeix a ERROR_CATALOG",
                    )


if __name__ == "__main__":
    unittest.main()
