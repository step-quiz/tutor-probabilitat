"""
Tutor de Grups Algebraics — UI Streamlit.

Per executar:
    export GEMINI_API_KEY=...
    streamlit run app.py

Mode debug: afegeix ?debug=1 a la URL.
"""

import os
import uuid
import streamlit as st

import problems as PB
import tutor as T
import llm as L
import api_logger

# Shorthand for localizing problem-data fields (tema, enunciat, step text)
_loc = PB.get_localized

# ============================================================
# Textos UI bilingües
# ============================================================
_UI = {
    "ca": {
        "page_title":       "Tutor IA — Probabilitat",
        "sidebar_title":    "🎲 Tutor Probabilitat",
        "problem_label":    "Problema:",
        "nickname_label":   "Pseudònim (opcional):",
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
        "signals_title":    "⌨️ Senyals especials",
        "signals_body":     "- `?` → pista socràtica\n- `!text` → registrar discrepància i avançar\n- `!!` → sortir de la sessió",
        "debug_caption":    "Mode debug actiu",
        "cost_label":       "Cost estimat (USD)",
        "calls_label":      "Crides OK / total",
        "select_problem":   "Selecciona un problema al panell esquerre i clica **▶ Iniciar**.",
        "prereq_title":     "### 🔁 Exercici de reforç previ",
        "answer_label":     "La teva resposta:",
        "submit_btn":       "Enviar ↵",
        "hint_btn":         "? Pista",
        "exit_btn":         "✕ Sortir",
        "solved":           "🎉 Problema completat! Has resolt el problema pas a pas.",
        "abandoned":        "Sessió tancada. Pots reiniciar quan vulguis.",
        "referred":         "Et recomanem parlar amb el professor o assistir a una tutoria.",
        "step_label":       "Pas {idx} de {total}",
        "discrepancy_msg":  "Discrepància anotada. Pas avançat.",
        "wrong_msg":        "Resposta no correcta.",
        "history_title":    "📋 Historial ({n} torns)",
        "history_student":  "*Alumne:*",
        "debug_title":      "🔍 Estat intern (debug)",
        "trace_title":      "Rastre JSON",
        "lang_label":       "Idioma / Language:",
        "send_label":       "Enviar",
    },
    "en": {
        "page_title":       "AI Tutor — Probability",
        "sidebar_title":    "🎲 Probability Tutor",
        "problem_label":    "Problem:",
        "nickname_label":   "Nickname (optional):",
        "start_btn":        "▶ Start problem",
        "notation_title":   "📐 Notation conventions",
        "notation_table":   r"""
| You want to write | Type |
|---|---|
| P(A) probability of A | `P(A)` |
| P(A ∩ B) intersection | `P(A and B)` or `P(A^B)` |
| P(A ∪ B) union | `P(A or B)` or `P(A v B)` |
| P(A \| B) conditional | `P(A\|B)` |
| Aᶜ complement | `A^c` or `not A` |
| Fraction 3/4 | `3/4` |
| Decimal 0.25 | `0.25` or `0,25` |
| Set {HH, HT, TH} | `{HH, HT, TH}` |
| Binomial coefficient C(n,k) | `C(n, k)` or `nCk` |
""",
        "signals_title":    "⌨️ Special signals",
        "signals_body":     "- `?` → Socratic hint\n- `!text` → log discrepancy and advance\n- `!!` → exit session",
        "debug_caption":    "Debug mode active",
        "cost_label":       "Estimated cost (USD)",
        "calls_label":      "OK calls / total",
        "select_problem":   "Select a problem on the left panel and click **▶ Start**.",
        "prereq_title":     "### 🔁 Prerequisite exercise",
        "answer_label":     "Your answer:",
        "submit_btn":       "Submit ↵",
        "hint_btn":         "? Hint",
        "exit_btn":         "✕ Exit",
        "solved":           "🎉 Problem complete! You have solved the problem step by step.",
        "abandoned":        "Session closed. You can restart whenever you like.",
        "referred":         "We recommend speaking with your lecturer or attending a tutorial session.",
        "step_label":       "Step {idx} of {total}",
        "discrepancy_msg":  "Discrepancy logged. Step advanced.",
        "wrong_msg":        "Incorrect answer.",
        "history_title":    "📋 History ({n} turns)",
        "history_student":  "*Student:*",
        "debug_title":      "🔍 Internal state (debug)",
        "trace_title":      "JSON trace",
        "lang_label":       "Idioma / Language:",
        "send_label":       "Submit",
    },
}


def _t(key: str) -> str:
    lang = st.session_state.get("lang", "ca")
    return _UI.get(lang, _UI["ca"]).get(key, key)


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

  .st-key-hint_btn button {
      background-color: #f59e0b !important;
      color: #ffffff !important;
      border: 1px solid #d97706 !important;
  }
  .st-key-exit_btn button {
      background-color: #4a4a4a !important;
      color: #ffffff !important;
      border: 1px solid #2d2d2d !important;
  }
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
    if "lang" not in st.session_state:
        st.session_state.lang = "ca"
    if "lang_chosen" not in st.session_state:
        st.session_state.lang_chosen = False
    if "tutor_state" not in st.session_state:
        st.session_state.tutor_state = None
    if "input_counter" not in st.session_state:
        st.session_state.input_counter = 0
    if "student_id" not in st.session_state:
        st.session_state.student_id = f"A{uuid.uuid4().hex[:6].upper()}"
    if "retry_messages" not in st.session_state:
        st.session_state.retry_messages = []


init_state()

# ------------------------------------------------------------
# Pantalla de selecció d'idioma (primera visita)
# ------------------------------------------------------------
if not st.session_state.lang_chosen:
    st.markdown("<br>" * 3, unsafe_allow_html=True)
    col_c = st.columns([1, 2, 1])[1]
    with col_c:
        st.markdown("## 🎲 Tutor IA · Probabilitat")
        st.markdown("---")
        st.markdown("### Tria l'idioma / Choose language")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🇨🇦  Català", use_container_width=True):
                st.session_state.lang = "ca"
                st.session_state.lang_chosen = True
                st.rerun()
        with col2:
            if st.button("🇬🇧  English", use_container_width=True):
                st.session_state.lang = "en"
                st.session_state.lang_chosen = True
                st.rerun()
    st.stop()

# ------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------
with st.sidebar:
    st.title(_t("sidebar_title"))

    # Canvi d'idioma (sempre disponible)
    lang_options = {"ca": "🇨🇦 Català", "en": "🇬🇧 English"}
    new_lang = st.selectbox(
        _t("lang_label"),
        options=list(lang_options.keys()),
        format_func=lambda x: lang_options[x],
        index=0 if st.session_state.lang == "ca" else 1,
        key="lang_selector",
    )
    if new_lang != st.session_state.lang:
        st.session_state.lang = new_lang
        st.rerun()

    st.divider()

    problem_ids = PB.list_problems()
    problem_labels = {pid: f"{pid} — {_loc(PB.PROBLEMS[pid]['tema'], st.session_state.lang)}" for pid in problem_ids}

    selected_pid = st.selectbox(
        _t("problem_label"),
        options=problem_ids,
        format_func=lambda x: problem_labels[x],
        key="selected_problem",
    )

    student_id_input = st.text_input(
        _t("nickname_label"),
        value="",
        max_chars=20,
    )
    if student_id_input:
        st.session_state.student_id = student_id_input

    if st.button(_t("start_btn"), use_container_width=True, key="start_btn"):
        st.session_state.tutor_state = T.new_session_state(
            selected_pid, st.session_state.student_id
        )
        L.set_log_context(
            student_id=st.session_state.student_id,
            session_id=st.session_state.tutor_state["session_id"],
        )
        st.session_state.input_counter = 0
        st.rerun()

    st.divider()

    with st.expander(_t("notation_title")):
        st.markdown(_t("notation_table"))

    with st.expander(_t("signals_title")):
        st.markdown(_t("signals_body"))

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
lang = st.session_state.lang

if state is None:
    st.info(_t("select_problem"))
    st.stop()

problem = state["problem"]
verdict_final = state.get("verdict_final")

st.markdown(f"## {problem['id']} — {_loc(problem['tema'], lang)}")
st.markdown(f"> {_loc(problem['enunciat'], lang)}")
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
        st.markdown(f"**{_loc(prereq['question'], lang)}**")

        with st.form(key=f"prereq_form_{st.session_state.input_counter}"):
            answer = st.text_area(_t("answer_label"), height=80)
            submitted = st.form_submit_button(_t("submit_btn"))
        if submitted and answer.strip():
            new_state = T.process_turn(state, answer, lang=lang)
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

if verdict_final == "abandoned":
    st.warning(_t("abandoned"))
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
    st.markdown(f"> {_loc(step['text'], lang)}")

    history = state.get("history", [])
    if history:
        last = history[-1]
        verdict = last.get("verdict")
        if verdict == "correct":
            st.success(f"✓ {last.get('reason', 'Correcte' if lang == 'ca' else 'Correct')}")
        elif verdict in ("typical_error", "conceptual_gap"):
            label = last.get("error_label", "")
            raw_cat = PB.ERROR_CATALOG.get(label, "")
            cat_msg = _loc(raw_cat, lang) if raw_cat else ""
            reason = last.get("reason", "")
            display = cat_msg or reason or _t("wrong_msg")
            st.warning(display)
        elif verdict == "discrepancy":
            st.info(_t("discrepancy_msg"))

    with st.form(key=f"step_form_{step_idx}_{st.session_state.input_counter}"):
        answer = st.text_area(
            _t("answer_label"),
            height=100,
            key=f"answer_{step_idx}_{st.session_state.input_counter}",
        )
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            submitted = st.form_submit_button(_t("submit_btn"))
        with col2:
            hint_req = st.form_submit_button(_t("hint_btn"), key="hint_btn")
        with col3:
            exit_req = st.form_submit_button(_t("exit_btn"), key="exit_btn")

    if exit_req:
        new_state = T.process_turn(state, "!!", lang=lang)
        st.session_state.tutor_state = new_state
        st.rerun()

    if hint_req:
        new_state = T.process_turn(state, "?", lang=lang)
        st.session_state.tutor_state = new_state
        st.session_state.input_counter += 1
        for msg in new_state.get("messages", []):
            if msg["kind"] == "hint":
                st.info(f"💡 {msg['text']}")
        st.rerun()

    if submitted and answer.strip():
        new_state = T.process_turn(state, answer, lang=lang)
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
                "discrepancy": "purple",
            }.get(verdict, "grey")
            step_label = turn.get("step_id", "?")
            st.markdown(
                f"<span style='color:{color}'>**{'Pas' if lang == 'ca' else 'Step'} "
                f"{step_label}** — {verdict}</span>",
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
            "lang":               lang,
        })
    with col_b:
        st.subheader(_t("trace_title"))
        st.json(T.build_trace(state))
