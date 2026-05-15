"""
Tutor de Probabilitat — UI Streamlit.

Per executar:
    export GEMINI_API_KEY=...
    streamlit run app.py

Mode debug: afegeix ?debug=1 a la URL.

Aquesta UI és monolingüe (català) i anònima:
- No hi ha cap selector d'idioma.
- L'alumne no s'identifica de cap manera (ni pseudònim).
- No hi ha caràcters especials (`?`, `!`, `!!`): l'alumne només envia
  text que respon al pas actual. Les pistes són proactives (les
  decideix l'engine via `_handle_conceptual_gap`).
"""

import json
import os
import uuid
from datetime import datetime

import streamlit as st

import problems as PB
import tutor as T
import llm as L
import api_logger

# Shorthand per aplanar els camps bilingües de problems.py (tema,
# enunciat, text dels passos). Tot retorna sempre la versió catalana.
_loc = PB.get_localized

# ============================================================
# Textos UI (català)
# ============================================================
_UI = {
    "page_title":       "Tutor IA — Probabilitat",
    "sidebar_title":    "🎲 Tutor Probabilitat",
    "problem_label":    "Problema:",
    "start_btn":        "▶ Iniciar problema",
    "notation_title":   "📐 Convencions de notació",
    "notation_table":   r"""
| Vols escriure | Escriu |
|---|---|
| P(A) probabilitat de A | `P(A)` |
| P(A ∩ B) intersecció | `P(A and B)` o `P(A^B)` |
| P(A ∪ B) unió | `P(A or B)` o `P(A v B)` |
| P(A \| B) condicionada | `P(A\|B)` |
| Aᶜ complementari | `A^c` o `not A` |
| Fracció 3/4 | `3/4` |
| Decimal 0,25 | `0.25` o `0,25` |
| Conjunt {HH, HT, TH} | `{HH, HT, TH}` |
| Coeficient binomial C(n,k) | `C(n, k)` o `nCk` |
""",
    "debug_caption":    "Mode debug actiu",
    "cost_label":       "Cost estimat (USD)",
    "calls_label":      "Crides OK / total",
    "select_problem":   "Selecciona un problema al panell esquerre i clica **▶ Iniciar**.",
    "prereq_title":     "### 🔁 Exercici de reforç previ",
    "answer_label":     "La teva resposta:",
    "submit_btn":       "Enviar ↵",
    "clear_btn_help":   "Buida la resposta",
    "solved":           "🎉 Problema completat! Has resolt el problema pas a pas.",
    "referred":         "Et recomanem parlar amb el professor o assistir a una tutoria.",
    "step_label":       "Pas {idx} de {total}",
    "wrong_msg":        "Resposta no correcta.",
    "history_title":    "📋 Historial ({n} torns)",
    "history_student":  "*Alumne:*",
    "debug_title":      "🔍 Estat intern (debug)",
    "trace_title":      "Rastre JSON",
}


def _t(key: str) -> str:
    """Recupera un text d'interfície per clau. Sempre en català."""
    return _UI.get(key, key)


# ============================================================
# Snippets de notació matemàtica (Q1-Q4 del feature spec)
# ============================================================
# Botonera que apareix SOBRE el text_area dels passos `free_text`.
# Cada tupla és (label_visual, snippet_inserit). El label és simbòlic
# i universal; el snippet és el text que el `judge_step` ja entén
# (`and`, `|`, `or`, `not`) — vegeu llm.py::_SYSTEM_JUDGE.
#
# El símbol `□` (U+25A1, WHITE SQUARE) és placeholder visual: indica
# a l'alumne "omple aquí". No s'insereix res — només són els labels.
#
# Per què a `free_text` i no als altres `input_type`s: els passos
# numèrics (decimal/fraction/integer) demanen un valor, no una
# expressió. La notació `p(A|B)` només té sentit on l'alumne ha
# d'escriure raonament en prosa amb expressions probabilístiques.
NOTATION_SNIPPETS = [
    ("p(□ ∩ □)", "p(  and  )"),
    ("p(□ | □)", "p(  |  )"),
    ("p(□)",     "p()"),
    ("p(□ ∪ □)", "p(  or  )"),
    ("P(Ā)",     "p(not )"),
]


# ============================================================

def _is_debug_mode() -> bool:
    if "debug_mode" not in st.session_state:
        try:
            qp = st.query_params.get("debug")
        except Exception:
            qp = None
        st.session_state.debug_mode = (qp == "1")
    return st.session_state.debug_mode


st.set_page_config(
    page_title="Tutor IA — Probabilitat",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  hr { margin: 0.6rem 0 !important; }
  .block-container h3 { margin-top: 0.3rem !important; }
  .block-container { padding-top: 2rem !important; font-size: 1.1rem; }

  [data-testid="InputInstructions"] { display: none !important; }

  .step-correct  { border-left: 4px solid #22c55e; padding-left: 0.6rem; }
  .step-error    { border-left: 4px solid #ef4444; padding-left: 0.6rem; }
  .step-gap      { border-left: 4px solid #f97316; padding-left: 0.6rem; }
  .step-discrep  { border-left: 4px solid #8b5cf6; padding-left: 0.6rem; }

  .st-key-start_btn button {
      background-color: #bfdbfe !important;
      color: #1e3a5f !important;
      border: 1px solid #93c5fd !important;
      font-weight: 600 !important;
  }
  .st-key-start_btn button:hover {
      background-color: #3b82f6 !important;
      color: #ffffff !important;
      border: 2px solid #1d4ed8 !important;
  }

  /* ========================================================
     Sticky header: títol + enunciat sempre visibles al fer scroll
     ========================================================
     `st.container(key="enunciat_header")` afegeix al DOM una classe
     `.st-key-enunciat_header`; la fem sticky perquè quedi enganxada
     a dalt mentre l'alumne va baixant per veure els missatges i el
     formulari del pas. Top = 2.5rem perquè quedi just per sota de la
     barra superior nativa de Streamlit (que ara mantenim visible
     perquè el control de re-expansió del sidebar viu allà).
  */
  .st-key-enunciat_header {
      position: sticky;
      top: 2.5rem;
      background-color: white;
      z-index: 50;
      padding: 0.6rem 0.2rem;
      border-bottom: 1px solid #e5e7eb;
      margin-bottom: 0.75rem;
  }

  /* Títol del problema: ~40 % més petit que h2 (≈ 30px → ≈ 18px).
     Substitueix l'antic `## {id} — {tema}` que era massa dominant. */
  .enunciat-title {
      font-size: 1.15rem;
      font-weight: 700;
      color: #1f2937;
      line-height: 1.35;
      margin: 0;
      padding: 0;
  }

  /* Botó-hamburger per amagar/mostrar el cos de l'enunciat. Compacte
     i discret; viu a la mateixa filera que el títol. */
  .st-key-enunciat_toggle button {
      background-color: transparent !important;
      color: #4a5568 !important;
      border: 1px solid #cbd5e0 !important;
      padding: 0.15rem 0.6rem !important;
      min-height: auto !important;
      font-size: 1.1rem !important;
      line-height: 1 !important;
  }
  .st-key-enunciat_toggle button:hover {
      background-color: #f3f4f6 !important;
      color: #1f2937 !important;
  }

  /* Cos de l'enunciat quan està expandit. Estil discret per
     diferenciar-lo del títol però sense competir amb el contingut
     del pas actual a sota. */
  .enunciat-body {
      margin-top: 0.4rem;
      padding-left: 0.75rem;
      border-left: 3px solid #cbd5e0;
      color: #4a5568;
      font-size: 0.95rem;
      line-height: 1.5;
  }
  .enunciat-body p { margin-bottom: 0.4rem; }
  .enunciat-body p:last-child { margin-bottom: 0; }
</style>
""", unsafe_allow_html=True)

if not _is_debug_mode():
    st.markdown("""
    <style>
      [data-testid="stMainMenu"] { display: none !important; }
      [data-testid="stToolbar"]  { display: none !important; }
      footer                     { display: none !important; }

      /* IMPORTANT: NO amaguem `stHeader` completament. Si ho fem,
         el botó per re-expandir el sidebar (quan està plegat) també
         desapareix i l'alumne queda atrapat sense menú lateral.
         En comptes d'això, el fem transparent i compacte. */
      [data-testid="stHeader"] {
          background-color: transparent !important;
          height: 2.5rem !important;
      }
/* Streamlit <1.40 */
      [data-testid="stSidebarCollapsedControl"],
      /* Streamlit 1.40+ */
      [data-testid="stSidebarCollapseButton"],
      [data-testid="collapsedControl"],
      /* Cobertura genèrica: qualsevol botó dins la zona del sidebar col·lapsat */
      section[data-testid="stSidebarCollapsedControl"] button,
      button[kind="headerNoPadding"],
      [data-testid="stSidebarNav"] ~ * button {
          display: flex !important;
          visibility: visible !important;
          opacity: 1 !important;
          z-index: 1000 !important;
      }
    </style>
    """, unsafe_allow_html=True)


# ------------------------------------------------------------
# Inicialització
# ------------------------------------------------------------
def init_state():
    # Estat de la sessió tutoria. None abans del primer "Iniciar problema".
    if "tutor_state" not in st.session_state:
        st.session_state.tutor_state = None
    # Comptador per forçar el reset del text_area entre torns (Streamlit
    # no recrea el widget si la clau no canvia).
    if "input_counter" not in st.session_state:
        st.session_state.input_counter = 0
    # Si True, el cos de l'enunciat (no el títol) queda amagat al
    # header sticky perquè l'alumne tingui més alçada útil. Es
    # commuta amb el botó ☰. Default False: cal veure l'enunciat
    # quan es comença un problema nou.
    if "enunciat_collapsed" not in st.session_state:
        st.session_state.enunciat_collapsed = False
    # Buffer de missatges pendents de mostrar (errors transitoris de
    # connexió, etc.) que han de sobreviure al `st.rerun()`.
    if "retry_messages" not in st.session_state:
        st.session_state.retry_messages = []


init_state()


# ------------------------------------------------------------
# Helpers per al mode debug (Capa B: Test exhaustiu / 1-for-all)
# ------------------------------------------------------------
# Aquests helpers s'invoquen DES dels botons de la sidebar i muten
# `st.session_state` perquè el render dels resultats (a la columna
# principal) els pugui mostrar després del `st.rerun()`. Mateix
# patró que `tutor-eq/app.py::_run_test_and_store`.
def _run_test_exhaustiu_and_store(problem_id: str):
    """Executa el test exhaustiu d'un problema i guarda els resultats
    a `st.session_state.test_exhaustiu_results` per al renderitzat
    posterior. Crida real a la IA (cost API)."""
    progress_box = st.empty()

    def _on_progress(r_idx, n_r, i_idx, n_i):
        progress_box.info(
            f"Test exhaustiu: ronda {r_idx}/{n_r}, input {i_idx}/{n_i}…"
        )

    test_sid = uuid.uuid4().hex[:12]
    with st.spinner("Executant test exhaustiu…"):
        results = T.run_exhaustive_test(
            problem_id, on_progress=_on_progress, session_id=test_sid,
        )
    progress_box.empty()
    # IMPORTANT: aquesta crida ha d'agregar totes les crides del test.
    # Si `cost.calls_total == 0` però el test ha tingut respostes
    # `correct` a passos free_text, és símptoma del bug del
    # set_log_context (vegeu tutor.py::_run_exhaustive_test_inner).
    cost = api_logger.summarize_session(test_sid)
    st.session_state.test_exhaustiu_results = {
        "problem_id":  problem_id,
        "results":     results,
        "test_sid":    test_sid,
        "cost":        cost,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }


def _run_1forall_and_store():
    """Itera el test exhaustiu sobre TOTS els problemes amb casos no
    buits, genera un informe consolidat i el guarda a
    `st.session_state.test_1forall_report`. Cost API ~$0,02-0,15."""
    problems_with_cases = sorted(
        pid for pid in PB.TEST_CASES if PB.get_test_cases(pid)
    )
    n_problems_total = len(problems_with_cases)
    batch_sid = uuid.uuid4().hex[:12]
    progress_box = st.empty()
    report = {
        "schema_version":     1,
        "kind":               "test_1forall",
        "started_at":         datetime.now().isoformat(timespec="seconds"),
        "model":              getattr(L, "MODEL", "unknown"),
        "batch_session_id":   batch_sid,
        "problems":           [],
        "summary":            {},
    }
    n_done = 0
    n_err_api = 0
    with st.spinner(
        f"Test 1-for-all en marxa ({n_problems_total} problemes). "
        "No tanquis la pestanya…"
    ):
        for pid in problems_with_cases:
            n_done += 1
            progress_box.info(
                f"Test 1-for-all: problema {n_done}/{n_problems_total} ({pid})…"
            )
            entry = {
                "problem_id":                  pid,
                "tema":                        _loc(PB.PROBLEMS[pid].get("tema", "")),
                "errors_freqüents_declarats": list(
                    PB.PROBLEMS[pid].get("errors_freqüents", [])),
                "n_rounds":                   len(PB.get_test_cases(pid) or []),
                "results":                    None,
                "error_api":                  False,
                "error_message":              None,
                "sub_session_id":             None,
            }
            sub_sid = f"{batch_sid}_{pid}"
            try:
                entry["results"] = T.run_exhaustive_test(
                    pid, on_progress=None, session_id=sub_sid,
                )
                entry["sub_session_id"] = sub_sid
            except Exception as e:
                entry["error_api"] = True
                entry["error_message"] = f"{type(e).__name__}: {e}"
                n_err_api += 1
            report["problems"].append(entry)
    progress_box.empty()

    # Agregació per al resum del cap. Mismatches agrupats per problema:
    # és la primera capa de revisió quan un LLM auxiliar analitza
    # l'informe offline.
    n_items = 0
    n_items_match = 0
    n_items_exc = 0
    mismatches_by_problem = {}
    for pe in report["problems"]:
        if pe["error_api"] or pe["results"] is None:
            continue
        for rd in pe["results"]:
            for it in rd.get("items", []):
                n_items += 1
                if it.get("exception"):
                    n_items_exc += 1
                if it.get("match"):
                    n_items_match += 1
                else:
                    mismatches_by_problem.setdefault(
                        pe["problem_id"], []
                    ).append({
                        "round":            rd["round"],
                        "step_id":          rd["step_id"],
                        "input":            it["input"],
                        "expected":         it["expected"],
                        "got_verdict":      it.get("verdict"),
                        "got_error_label": it.get("error_label"),
                        "exception":        it.get("exception"),
                    })

    # Cost agregat — sumant els sub_session_id de cada problema.
    total_calls, total_tin, total_tout, total_cost = 0, 0, 0, 0.0
    for pe in report["problems"]:
        sid = pe.get("sub_session_id")
        if not sid:
            continue
        try:
            s = api_logger.summarize_session(sid)
            total_calls += s.get("calls_total", 0)
            total_tin   += s.get("tokens_input", 0)
            total_tout  += s.get("tokens_output", 0)
            total_cost  += s.get("cost_usd", 0.0)
        except Exception:
            pass

    report["summary"] = {
        "n_problems_total":      n_problems_total,
        "n_problems_ok":         n_problems_total - n_err_api,
        "n_problems_error_api":  n_err_api,
        "n_items_total":         n_items,
        "n_items_match":         n_items_match,
        "n_items_mismatch":      n_items - n_items_match,
        "n_items_exception":     n_items_exc,
        "mismatches_by_problem": mismatches_by_problem,
        "cost": {
            "calls_total":   total_calls,
            "tokens_input":  total_tin,
            "tokens_output": total_tout,
            "cost_usd":      round(total_cost, 6),
        },
    }
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    st.session_state.test_1forall_report = report


# ------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------
with st.sidebar:
    st.title(_t("sidebar_title"))

    problem_ids = PB.list_problems()
    problem_labels = {
        pid: f"{pid} — {_loc(PB.PROBLEMS[pid]['tema'])}"
        for pid in problem_ids
    }

    selected_pid = st.selectbox(
        _t("problem_label"),
        options=problem_ids,
        format_func=lambda x: problem_labels[x],
        key="selected_problem",
    )

    if st.button(_t("start_btn"), use_container_width=True, key="start_btn"):
        # Sessió anònima: `new_session_state` ja propaga el `session_id`
        # al log de l'API amb `student_id=None`. No cal cridar
        # `set_log_context` aquí.
        st.session_state.tutor_state = T.new_session_state(selected_pid)
        st.session_state.input_counter = 0
        st.rerun()

    st.divider()

    with st.expander(_t("notation_title")):
        st.markdown(_t("notation_table"))

    if _is_debug_mode():
        st.divider()
        st.caption(_t("debug_caption"))
        state = st.session_state.tutor_state
        if state:
            summary = api_logger.summarize_session(session_id=state["session_id"])
            st.metric(_t("cost_label"), f"${summary['cost_usd']:.4f}")
            st.metric(_t("calls_label"),
                      f"{summary['calls_ok']} / {summary['calls_total']}")

        # =========================================================
        # CAPA B — llançadors de Test exhaustiu i Test 1-for-all
        # =========================================================
        # Mateix patró que tutor-eq: botons a la sidebar, render dels
        # resultats a la columna principal. La sidebar és estreta i no
        # encabeix bé taules d'expanders.
        st.divider()
        st.markdown("**🧪 Test exhaustiu**")
        if state:
            rounds = PB.get_test_cases(state["problem_id"]) or []
            n_items = sum(len(r) for r in rounds)
            if n_items == 0:
                st.caption(
                    f"Cap cas definit per a `{state['problem_id']}`."
                )
            else:
                st.caption(
                    f"{n_items} input(s) · {len(rounds)} ronda(es). "
                    f"Crida real a IA als passos `free_text`."
                )
                if st.button("🧪 Test exhaustiu",
                             key="test_exh_btn",
                             use_container_width=True):
                    _run_test_exhaustiu_and_store(state["problem_id"])
                    st.rerun()
                if st.session_state.get("test_exhaustiu_results"):
                    if st.button("Tanca resultats",
                                 key="test_exh_clear",
                                 use_container_width=True):
                        st.session_state.test_exhaustiu_results = None
                        st.rerun()
        else:
            st.caption("Inicia un problema per veure aquest test.")

        # ---- Test 1-for-all ----
        st.divider()
        st.markdown("**🧪 Test 1-for-all**")
        problems_with_cases = sorted(
            pid for pid in PB.TEST_CASES if PB.get_test_cases(pid)
        )
        n_problems_total = len(problems_with_cases)
        if n_problems_total == 0:
            st.caption("Cap problema amb casos definits.")
        else:
            st.caption(
                f"{n_problems_total}/{len(PB.TEST_CASES)} problemes amb casos. "
                f"Cost API moderat."
            )
            awaiting = st.session_state.get("awaiting_1forall_confirm", False)
            if not awaiting:
                if st.button("🧪 Test 1-for-all",
                             key="test_1f_btn",
                             use_container_width=True):
                    st.session_state.awaiting_1forall_confirm = True
                    st.rerun()
            else:
                st.warning(
                    "Confirmes? Cost ~$0,02-0,15 amb gemini-2.5-flash."
                )
                col_y, col_n = st.columns(2)
                with col_y:
                    if st.button("✅ Sí",
                                 key="test_1f_accept",
                                 use_container_width=True):
                        st.session_state.awaiting_1forall_confirm = False
                        _run_1forall_and_store()
                        st.rerun()
                with col_n:
                    if st.button("❌ No",
                                 key="test_1f_cancel",
                                 use_container_width=True):
                        st.session_state.awaiting_1forall_confirm = False
                        st.rerun()
            if st.session_state.get("test_1forall_report"):
                if st.button("Tanca informe",
                             key="test_1f_clear",
                             use_container_width=True):
                    st.session_state.test_1forall_report = None
                    st.rerun()

        st.caption(f"Log: `{api_logger.get_log_path()}`")

# ------------------------------------------------------------
# Panell principal
# ------------------------------------------------------------
state = st.session_state.tutor_state

if state is None:
    st.info(_t("select_problem"))
    st.stop()

problem = state["problem"]
verdict_final = state.get("verdict_final")

# ------------------------------------------------------------
# Header sticky: títol + enunciat sempre presents al fer scroll.
# ------------------------------------------------------------
# Tres components dins del mateix contenidor:
#   1. Títol compacte (custom CSS, ~40 % més petit que h2)
#   2. Botó hamburger (☰) per amagar/mostrar el cos
#   3. Cos de l'enunciat (només si no està collapsed)
#
# L'efecte sticky el dona el CSS de `.st-key-enunciat_header` definit
# a l'stylesheet global més amunt. Streamlit injecta automàticament
# aquesta classe al div del contenidor perquè li hem passat `key=`.
with st.container(key="enunciat_header"):
    col_title, col_toggle = st.columns([20, 1])
    with col_title:
        st.markdown(
            f"<div class='enunciat-title'>"
            f"{problem['id']} — {_loc(problem['tema'])}"
            f"</div>",
            unsafe_allow_html=True,
        )
    with col_toggle:
        # Etiqueta del botó canvia segons l'estat: hamburger quan es
        # pot collapsar, fletxa avall quan està collapsed (per
        # indicar que es desplegarà).
        toggle_label = "▾" if st.session_state.enunciat_collapsed else "☰"
        toggle_help = (
            "Mostra l'enunciat"
            if st.session_state.enunciat_collapsed
            else "Amaga l'enunciat per guanyar espai vertical"
        )
        if st.button(toggle_label, key="enunciat_toggle", help=toggle_help):
            st.session_state.enunciat_collapsed = not st.session_state.enunciat_collapsed
            st.rerun()

    if not st.session_state.enunciat_collapsed:
        # Renderitzem amb st.markdown per preservar el format del
        # camp (negretes als marcadors **a)**, **b)**, etc.). El
        # CSS `.enunciat-body` aplica la vora esquerra i el color
        # discret. Tota la sortida queda dins del mateix contenidor
        # sticky perquè es desplaci amb el títol.
        st.markdown(
            f"<div class='enunciat-body'>\n\n{_loc(problem['enunciat'])}\n\n</div>",
            unsafe_allow_html=True,
        )

for msg in state.get("messages", []):
    kind = msg["kind"]
    text = msg["text"]
    if kind == "system":
        st.info(text)
    elif kind == "hint":
        st.info(f"💡 {text}")
    elif kind in ("prereq_resolved",):
        st.success(text)
    elif kind in ("prereq_failed",):
        st.warning(text)
    elif kind == "warning":
        st.error(text)

if state.get("active_prereq"):
    prereq = PB.get_prerequisite(state["active_prereq"])
    if prereq:
        st.markdown("---")
        st.markdown(_t("prereq_title"))
        st.markdown(f"**{_loc(prereq['question'])}**")

        with st.form(key=f"prereq_form_{st.session_state.input_counter}"):
            answer = st.text_area(_t("answer_label"), height=80)
            submitted = st.form_submit_button(_t("submit_btn"))
        if submitted and answer.strip():
            new_state = T.process_turn(state, answer)
            st.session_state.tutor_state = new_state
            st.session_state.input_counter += 1
            st.rerun()
        st.stop()

if verdict_final == "solved":
    st.success(_t("solved"))
    if _is_debug_mode():
        st.subheader(_t("trace_title"))
        st.json(T.build_trace(state))
    st.stop()

if verdict_final == "referred_to_tutor":
    st.error(_t("referred"))
    st.stop()

if verdict_final == "suspended":
    # Sessió tancada per ús inadequat (3 avisos sense contingut matemàtic).
    # Els missatges del `_handle_inappropriate` ja s'han renderitzat al
    # bloc de `state["messages"]` més amunt; aquí només aturem el render
    # del formulari de pas.
    st.stop()

steps = problem["passos"]
step_idx = state["current_step_idx"]

if step_idx < len(steps):
    step = steps[step_idx]
    st.markdown(f"**{_t('step_label').format(idx=step['id'], total=len(steps))}**")
    st.markdown(f"> {_loc(step['text'])}")

    history = state.get("history", [])
    if history:
        last = history[-1]
        verdict = last.get("verdict")
        if verdict == "correct":
            st.success(f"✓ {last.get('reason', 'Correcte')}")
        elif verdict == "incomplete":
            # Subconjunt correcte de l'esperat: ho mostrem com a info
            # (no error). Combinem el reconeixement del que va bé amb
            # la re-pregunta socràtica del judge per al que falta.
            reason = last.get("reason", "") or ""
            nq = last.get("next_question", "") or ""
            text = f"{reason} {nq}".strip() or "Vas bé però falten elements del pas."
            st.info(text)
        elif verdict in ("typical_error", "conceptual_gap"):
            label = last.get("error_label", "")
            raw_cat = PB.ERROR_CATALOG.get(label, "")
            cat_msg = _loc(raw_cat) if raw_cat else ""
            reason = last.get("reason", "")
            display = cat_msg or reason or _t("wrong_msg")
            st.warning(display)

    # ====================================================================
    # Bifurcació del render del pas segons `input_type`:
    #
    # - `free_text` → camí SENSE st.form: text_area amb `key` explícit
    #   (perquè Streamlit sincronitzi el valor amb session_state a cada
    #   interacció) + botonera de snippets de notació al damunt + botó
    #   paperera + botó submit. Sortir del form és necessari perquè els
    #   botons de snippet són `st.button` regulars, i un botó fora del
    #   form que es clica DURANT l'edició PERDRIA el text si el text_area
    #   estigués dins d'un form (els forms només commiten al submit).
    #
    # - integer / decimal / fraction / set_listing → camí AMB st.form
    #   (com abans). Aquests passos demanen un valor, no una expressió,
    #   així que no necessiten snippets de notació, i el form els dóna
    #   suport a Cmd+Enter per enviar.
    # ====================================================================
    it = step.get("input_type", "free_text")
    answer_key = f"answer_{step_idx}_{st.session_state.input_counter}"

    if it == "free_text":
        # Botonera de snippets de notació matemàtica. Cada botó fa
        # `session_state[answer_key] += snippet` i un rerun. Streamlit
        # accepta perfectament aquesta mutació perquè el text_area es
        # renderitza DESPRÉS, llegint el valor actualitzat del state.
        snip_cols = st.columns(len(NOTATION_SNIPPETS))
        for i, (label, snippet) in enumerate(NOTATION_SNIPPETS):
            with snip_cols[i]:
                if st.button(
                    label,
                    key=f"snip_{i}_{step_idx}_{st.session_state.input_counter}",
                    use_container_width=True,
                ):
                    current = st.session_state.get(answer_key, "")
                    st.session_state[answer_key] = current + snippet
                    st.rerun()

        st.text_area(
            _t("answer_label"),
            height=100,
            key=answer_key,
        )

        # Fila d'accions: submit (gros, esquerra) + paperera (petita, dreta).
        # Ratio 5:1 perquè la paperera no domini visualment — és una acció
        # secundària. Confirmació no cal: si l'alumne s'equivoca, és tornar
        # a escriure (o tornar a clicar snippets).
        col_submit, col_clear = st.columns([5, 1])
        with col_submit:
            submitted = st.button(
                _t("submit_btn"),
                key=f"submit_{step_idx}_{st.session_state.input_counter}",
                use_container_width=True,
                type="primary",
            )
        with col_clear:
            if st.button(
                "🗑️",
                key=f"clear_{step_idx}_{st.session_state.input_counter}",
                help=_t("clear_btn_help"),
                use_container_width=True,
            ):
                st.session_state[answer_key] = ""
                st.rerun()

        # El valor enviat NO es llegeix de la variable retornada per
        # st.text_area (que en aquest patró sense form ja no s'usa),
        # sinó del session_state on Streamlit l'ha sincronitzat.
        answer = st.session_state.get(answer_key, "")

    else:
        # Camí clàssic per a passos amb input numèric o de conjunt.
        # El form proporciona Cmd+Enter per a enviament, i com que
        # aquests passos no requereixen snippets, l'aïllament del form
        # no és un problema.
        with st.form(key=f"step_form_{step_idx}_{st.session_state.input_counter}"):
            answer = st.text_area(
                _t("answer_label"),
                height=100,
                key=answer_key,
            )
            submitted = st.form_submit_button(_t("submit_btn"))

    if submitted and answer.strip():
        new_state = T.process_turn(state, answer)
        st.session_state.tutor_state = new_state
        st.session_state.input_counter += 1
        st.rerun()

history = state.get("history", [])
if history:
    st.divider()
    with st.expander(_t("history_title").format(n=len(history)), expanded=False):
        for turn in reversed(history):
            verdict = turn.get("verdict", "")
            color = {
                "correct": "green",
                "incomplete": "blue",
                "typical_error": "orange",
                "conceptual_gap": "red",
                "no_math": "purple",
            }.get(verdict, "grey")
            step_label = turn.get("step_id", "?")
            st.markdown(
                f"<span style='color:{color}'>**Pas {step_label}** — {verdict}</span>",
                unsafe_allow_html=True,
            )
            student_text = turn.get("student", turn.get("text", ""))
            if student_text:
                st.markdown(f"{_t('history_student')} {student_text}")
            reason = turn.get("reason", "")
            if reason:
                st.caption(reason)
            st.markdown("---")

if _is_debug_mode():
    st.divider()
    st.subheader(_t("debug_title"))
    col_a, col_b = st.columns(2)
    with col_a:
        st.json({
            "problem_id":         state["problem_id"],
            "current_step_idx":   state["current_step_idx"],
            "backtrack_depth":    state["backtrack_depth"],
            "backtrack_count":    state["backtrack_count"],
            "active_prereq":      state["active_prereq"],
            "verdict_final":      state["verdict_final"],
            "nodes_consolidated": state["nodes_consolidated"],
            "concept_failure_streak": state["concept_failure_streak"],
            "inappropriate_warnings": state["inappropriate_warnings"],
            "step_partials":      state.get("step_partials", []),
        })
    with col_b:
        st.subheader(_t("trace_title"))
        st.json(T.build_trace(state))

    # =====================================================================
    # CAPA B — Render dels resultats dels tests (només mode debug)
    # =====================================================================
    # Els BOTONS de llançament ("🧪 Test exhaustiu" i "🧪 Test 1-for-all")
    # estan a la SIDEBAR (vegeu el bloc `if _is_debug_mode():` allí). Aquí
    # només mostrem els resultats persistits a `st.session_state`. Mateix
    # patró que `tutor-eq/app.py`: la sidebar és estreta i no encabeix bé
    # les expanders amb taules; per això el render va a la columna
    # principal.

    # ---- Resultats del Test exhaustiu del problema actual --------------
    exh = st.session_state.get("test_exhaustiu_results")
    if exh and exh.get("problem_id") == state["problem_id"]:
        st.divider()
        st.markdown("### 🧪 Resultats — Test exhaustiu")
        results = exh["results"]
        cost = exh["cost"]

        # Resum: matches / mismatches / excepcions
        n_total = n_match = n_exc = 0
        for r in results:
            for it in r.get("items", []):
                n_total += 1
                if it.get("exception"):
                    n_exc += 1
                if it.get("match"):
                    n_match += 1
        mismatches = n_total - n_match
        ok_msg = f"{n_match}/{n_total} matches"
        if mismatches:
            ok_msg += f" · {mismatches} mismatch(es)"
        if n_exc:
            ok_msg += f" · {n_exc} excepció/ns"
        st.markdown(
            f"**{ok_msg}** · cost: {cost['calls_total']} crides · "
            f"~${cost['cost_usd']:.4f}"
        )

        # Una expander per ronda. Col·lapsada si tot match; expandida si
        # hi ha mismatch (per cridar l'atenció).
        for r in results:
            items = r.get("items", [])
            if not items:
                continue
            round_match = sum(1 for it in items if it["match"])
            round_total = len(items)
            icon = "✅" if round_match == round_total else "⚠️"
            label = (
                f"{icon} Ronda {r['round']} — pas {r['step_id']} "
                f"({round_match}/{round_total})"
            )
            with st.expander(label, expanded=(round_match != round_total)):
                if r.get("schema_warning"):
                    st.warning(r["schema_warning"])
                for it in items:
                    st.markdown(
                        f"- {'✅' if it['match'] else '❌'} "
                        f"`{it['input']}` → expected "
                        f"**{it['expected']}**"
                        + (f" (`{it['expected_error_label']}`)"
                           if it.get("expected_error_label") else "")
                        + f", got **{it['verdict']}**"
                        + (f" (`{it['error_label']}`)"
                           if it.get("error_label") else "")
                    )
                    if it.get("exception"):
                        st.error(f"Excepció: `{it['exception']}`")
                    if it.get("missing") or it.get("next_question"):
                        st.caption(
                            f"missing: {it.get('missing')!r} · "
                            f"next_question: {it.get('next_question')!r}"
                        )
                    if it.get("rationale"):
                        st.caption(f"_Justificació del cas:_ {it['rationale']}")

        st.download_button(
            "⬇️ Descarrega JSON",
            data=json.dumps(exh, ensure_ascii=False, indent=2),
            file_name=(
                f"test_exhaustiu_{state['problem_id']}_"
                f"{exh['test_sid']}.json"
            ),
            mime="application/json",
            key="test_exhaustiu_download",
        )

    # ---- Resultats del Test 1-for-all (informe consolidat) -------------
    rep = st.session_state.get("test_1forall_report")
    if rep:
        st.divider()
        st.markdown("### 🧪 Informe — Test 1-for-all")
        summ = rep["summary"]
        st.success(
            f"Test acabat: {summ['n_problems_ok']}/{summ['n_problems_total']} "
            f"problemes OK · {summ['n_items_match']}/{summ['n_items_total']} "
            f"matches · cost ~${summ['cost']['cost_usd']:.4f}"
        )
        if summ["mismatches_by_problem"]:
            st.warning(
                f"Mismatches a {len(summ['mismatches_by_problem'])} "
                f"problema(es). Veure JSON per al detall."
            )
        st.download_button(
            "⬇️ Descarrega informe JSON",
            data=json.dumps(rep, ensure_ascii=False, indent=2),
            file_name=f"test_1forall_{rep['batch_session_id']}.json",
            mime="application/json",
            key="test_1forall_download",
        )
