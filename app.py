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

import os
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
</style>
""", unsafe_allow_html=True)

if not _is_debug_mode():
    st.markdown("""
    <style>
      [data-testid="stMainMenu"] { display: none !important; }
      [data-testid="stToolbar"]  { display: none !important; }
      [data-testid="stHeader"]   { display: none !important; }
      footer                     { display: none !important; }
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
    # Buffer de missatges pendents de mostrar (errors transitoris de
    # connexió, etc.) que han de sobreviure al `st.rerun()`.
    if "retry_messages" not in st.session_state:
        st.session_state.retry_messages = []


init_state()

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

# ------------------------------------------------------------
# Panell principal
# ------------------------------------------------------------
state = st.session_state.tutor_state

if state is None:
    st.info(_t("select_problem"))
    st.stop()

problem = state["problem"]
verdict_final = state.get("verdict_final")

st.markdown(f"## {problem['id']} — {_loc(problem['tema'])}")
st.markdown(f"> {_loc(problem['enunciat'])}")
st.divider()

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
        elif verdict in ("typical_error", "conceptual_gap"):
            label = last.get("error_label", "")
            raw_cat = PB.ERROR_CATALOG.get(label, "")
            cat_msg = _loc(raw_cat) if raw_cat else ""
            reason = last.get("reason", "")
            display = cat_msg or reason or _t("wrong_msg")
            st.warning(display)

    # Formulari del pas: només un botó d'envia. Sense `?`/`!!` ni
    # discrepància — la UI accepta exclusivament text que respon al pas.
    with st.form(key=f"step_form_{step_idx}_{st.session_state.input_counter}"):
        answer = st.text_area(
            _t("answer_label"),
            height=100,
            key=f"answer_{step_idx}_{st.session_state.input_counter}",
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
        })
    with col_b:
        st.subheader(_t("trace_title"))
        st.json(T.build_trace(state))
