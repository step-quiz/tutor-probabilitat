"""
Màquina d'estats del tutor de Probabilitat (batxillerat).

Visió general per a un lector nou
=================================
Aquest mòdul implementa tota la lògica de control d'una sessió de tutoria
**sense dependre de Streamlit**. La UI (app.py) només crida `process_turn`
i llegeix l'estat retornat. Això permet provar la lògica amb scripts o
amb tests sense aixecar la UI.

L'estat de la sessió és un `dict` (no una classe) que es construeix amb
`new_session_state(problem_id)` i s'actualitza en cada torn amb
`process_turn(state, raw_input) → state`. L'esquema complet de l'estat
està documentat a `SCHEMA.md`.

La interfície és monolingüe (català). No hi ha cap paràmetre `lang`:
tots els missatges generats per l'engine són en català, i els camps
bilingües de `problems.py` s'aplanen via `PB.get_localized(field)` que
sempre retorna la versió catalana.

Components principals
---------------------
- `process_turn` — punt d'entrada únic. Despatxa per `step["input_type"]`.
- `_check_numeric` / `_check_integer` / `_check_set` — verificadors
  deterministes per als passos `decimal`/`fraction`, `integer` i
  `set_listing`. Retornen `True`/`False` quan poden parsejar, o `None`
  per delegar a la IA com a fallback.
- `_has_math_content` — heurística determinista per detectar input
  no-matemàtic (ús inadequat del sistema). Portat de `tutor-eq`.
- `_handle_inappropriate` — comptador `inappropriate_warnings`. Al
  tercer avís, la sessió es marca `suspended`.
- `_handle_conceptual_gap` — quan la IA marca buit conceptual: decideix
  si toca retrocedir a un prerequisit o si ja s'ha esgotat el límit.
  Si el `_quick_keyword_check` detecta que l'alumne coneix el concepte,
  genera una pista proactiva (via `L.generate_hint`) en comptes de
  retrocedir. Aquesta és l'única via per la qual es produeixen pistes
  automàticament — no hi ha cap senyal d'usuari per demanar-ne.
- `_process_prereq_turn` — quan estem dins d'una mini-sessió de
  prerequisit, avalua la resposta de manera determinista (keyword match).
- `build_trace` / `serialize_trace` — generació del rastre JSON per al
  professor a la fi de la sessió.

Senyals d'usuari
----------------
L'alumne envia text lliure. NO hi ha caràcters especials (`?`, `!`, `!!`).
La sessió només acaba per camí natural: tots els passos correctes
(`solved`), tres avisos d'ús inadequat (`suspended`), o
`MAX_BACKTRACK_DEPTH` retrocessos a prerequisits sense desbloquejar
(`referred_to_tutor`).

Diferència principal amb `tutor-grups`
--------------------------------------
- Suport per a `input_type` numèric (`decimal`, `fraction`, `integer`)
  amb verificació determinista via `fractions.Fraction`. Si l'input no
  parseja, es delega a `llm.judge_step` com a fallback.
- Detecció determinista d'ús inadequat (portada de `tutor-eq`).
- Monolingüe (català). Sense identificació de l'alumne.
"""

import copy
import json
import re
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from fractions import Fraction as _Frac

import problems as PB
import llm as L

# Profunditat màxima del retrocés a prerequisits.
#
# Quan un alumne mostra un buit conceptual, el sistema l'envia a un
# mini-exercici de reforç (prerequisit). Si dins d'aquell mini-exercici
# torna a fallar conceptualment, podríem retrocedir a un altre prereq
# encara més bàsic. Aquest valor és el sostre: si s'arriba a profunditat
# 2 i encara falla, la sessió es marca com a `referred_to_tutor`
# (l'alumne necessita ajuda humana).
MAX_BACKTRACK_DEPTH = 2

# Sostre d'avisos d'ús inadequat. Cada torn amb input no-matemàtic
# (definit per `_has_math_content`) incrementa el comptador. Al tercer
# avís, la sessió es marca `suspended` automàticament. Aquest mecanisme
# està portat de `tutor-eq` (vegeu el briefing §4a).
MAX_INAPPROPRIATE_WARNINGS = 3


# ============================================================
# Construcció d'un estat nou
# ============================================================
def new_session_state(problem_id: str) -> dict:
    """
    Estat inicial d'una sessió per a un problema concret.

    No es demana cap identificador de l'alumne: tot el sistema treballa
    de manera anònima. Cada sessió queda marcada al log de l'API
    només pel `session_id` (12 hex chars) generat aquí. Una sessió =
    un intent d'un problema, no la vida del procés Python.

    L'esquema complet del dict retornat està a `SCHEMA.md` §"Estat de sessió".
    """
    problem = PB.get_problem(problem_id)
    session_id = uuid.uuid4().hex[:12]
    # Propaguem el `session_id` al logger; `student_id=None` perquè
    # l'alumne és anònim. `_current_student_id()` farà fallback a
    # "anon" automàticament als logs de l'API.
    L.set_log_context(student_id=None, session_id=session_id)
    return {
        "session_id":             session_id,
        # L'alumne és anònim per disseny. El camp es manté al dict per
        # estabilitat de l'esquema (lectors externs del trace JSON
        # poden esperar-lo); sempre val None.
        "student_id":             None,
        "problem_id":             problem_id,
        "problem":                problem,
        "started_at":             datetime.now(timezone.utc).isoformat(),
        "started_at_ts":          time.time(),
        # Índex al `problem["passos"]`. Es queda a `len(passos)` un cop
        # resolt; `_maybe_finish` ho usa per detectar la fi.
        "current_step_idx":       0,
        # Llista de torns gravats per `process_turn`. Cada torn és un
        # dict {type, step_id, student, verdict, error_label, reason, ts}.
        # Es serialitza al rastre JSON.
        "history":                [],
        # Comptador d'errors consecutius (sense distingir tipus) per
        # detectar estancament. Actualment només s'usa de manera defensiva
        # (no dispara pista proactiva — això és el que fa tutor-eq amb
        # `pending_proactive_offer`, pendent aquí).
        "stagnation_consecutive": 0,
        # Comptadors del retrocés a prereqs (per al rastre JSON i per al
        # límit de profunditat).
        "backtrack_count":        0,   # nº total de retrocessos
        "backtrack_depth":        0,   # profunditat actual del retrocés
        # Si != None, estem dins d'una mini-sessió de prerequisit (PRE-XXX).
        # Tots els torns en aquest estat van a `_process_prereq_turn` en
        # lloc del flux normal d'avaluació de pas.
        "active_prereq":          None,
        "active_prereq_depth":    0,
        # Comptador d'errors consecutius per concepte (clau = dep_id, valor
        # = nº fallades sense un correct entremig). Permet escalar l'ajuda:
        # 1ª errada conceptual → prereq, 2ª → pista socràtica directa.
        # Es reseteja amb un pas correcte.
        "concept_failure_streak": {},
        # Respostes parcials de l'alumne dins del pas ACTUAL, en ordre. Quan
        # `judge_step` marca `incomplete` (la resposta és un subconjunt
        # correcte de l'esperada), la guardem aquí i la passem al judge del
        # proper torn perquè pugui validar la unió cumulativa. Es reseteja a
        # `[]` quan el pas s'avança per un veredicte `correct`. NO es
        # reseteja amb `typical_error` ni `conceptual_gap`: les parcials
        # vàlides anteriors segueixen valent.
        "step_partials":          [],
        # None mentre la sessió segueix. Valors finals possibles:
        # - "solved"             → tots els passos correctes
        # - "referred_to_tutor"  → MAX_BACKTRACK_DEPTH assolit
        # - "suspended"          → 3 avisos d'ús inadequat
        "verdict_final":          None,
        # Nodes del DAG que l'alumne ha consolidat (resolent el prereq
        # corresponent). Per al rastre i analítica futura del pilot.
        "nodes_consolidated":     [],
        # Slot reservat per a un missatge pendent (no s'usa al codi viu).
        "pending_message":        None,
        # Comptador d'avisos per ús inadequat (input sense contingut
        # matemàtic). Portat de `tutor-eq`. Al 3r avís, la sessió
        # es marca `suspended`. Es reseteja a 0 quan torna a haver-hi
        # contingut matemàtic.
        "inappropriate_warnings": 0,
        # Missatges UI per al torn actual. Cada element: {kind, text,
        # persistent, ts}. Es netegen al començament de cada torn excepte
        # els marcats com a `persistent` (típicament feedback de tancament
        # de prereq, que l'alumne ha de seguir veient mentre torna a
        # intentar l'original).
        "messages":               [],
    }


# ============================================================
# Helpers de missatge
# ============================================================
def _push_msg(state, kind: str, text: str, persistent: bool = False):
    """
    Encua un missatge per a la UI.

    `kind` indica com el renderitza app.py:
      - "system"           → st.info amb to neutre
      - "feedback"         → comentari sobre el pas (correcte / incorrecte)
      - "hint"             → pista, prefixada amb 💡
      - "warning"          → error tècnic o avís de fi
      - "prereq"           → inici d'un mini-exercici de reforç
      - "prereq_resolved"  → feedback positiu de tancament de prereq (verd)
      - "prereq_failed"    → feedback negatiu de tancament de prereq (groc)

    `persistent`: si True, sobreviu al reset de missatges del proper torn.
    S'usa per als feedbacks de tancament de prereq, perquè l'alumne segueixi
    veient-los mentre intenta aplicar el que ha après al problema principal.
    """
    state["messages"].append({
        "kind": kind,
        "text": text,
        "persistent": persistent,
        "ts": time.time(),
    })


# ============================================================
# Comprovació determinista de prerequisit (keyword matching)
# ============================================================
def _quick_keyword_check(dep_id: str, student_answer: str) -> bool:
    """
    Verificació ràpida i sense cost: la resposta de l'alumne conté alguna
    paraula clau associada al concepte? Si sí, assumim que el coneix però
    no l'aplica correctament. En aquest cas no calia retrocedir a prereq,
    sinó donar pista socràtica.

    NOTA: La comparació és per substring sense word-boundary, igual que a
    `_process_prereq_turn`. Pot tenir falsos positius (p.ex. "subgrup" dins
    de "subgrupador"), però per al domini i mida de respostes és acceptable.
    """
    dep = PB.get_dependency(dep_id)
    if not dep:
        return False
    s_low = student_answer.lower()
    keywords = dep.get("keywords", [])
    return any(kw.lower() in s_low for kw in keywords)


# ============================================================
# Verificadors deterministes per als input_type numèrics
# ============================================================
# Per als passos amb input_type ∈ {"integer", "decimal", "fraction",
# "set_listing"}, la comparació amb l'expected_value es fa aquí sense
# cridar la IA. Estalviem cost i guanyem determinisme. Si l'input no
# es pot parsejar a un valor del tipus esperat, retornem None per
# delegar al `L.judge_step` com a fallback (l'alumne pot haver escrit
# raonament en text lliure malgrat que el pas demanés un número).
def _check_integer(raw_text: str, expected_value) -> "bool | None":
    """
    Comprova un input enter contra `expected_value`. Accepta nombres amb
    signes, espais i comes decimals (que es rebutgen si la part decimal
    no és zero). Retorna True/False quan parseja; None si l'input no és
    un enter clarament identificable (delega a la IA).
    """
    if raw_text is None:
        return None
    s = raw_text.strip().replace(",", ".").replace(" ", "")
    # Cas habitual: dígits opcionalment amb signe.
    try:
        as_float = float(s)
    except (ValueError, TypeError):
        return None
    if not float(as_float).is_integer():
        return None
    try:
        return int(as_float) == int(expected_value)
    except (ValueError, TypeError):
        return None


def _check_numeric(raw_text: str, expected_value) -> "bool | None":
    """
    Comprova un input numèric (decimal o fracció) contra `expected_value`.

    Accepta:
      - decimals amb punt o coma: "0.375", "0,375"
      - fraccions: "3/8", "9/19"
      - mixtos sense parèntesis: "3 / 8" (espais s'eliminen)

    Tolerància: |student − expected| < 1e-4 (≈ "fins a la quarta
    decimal"). Aquest llindar accepta aproximacions de 4 decimals
    raonables (p.ex., "0.4737" per a 9/19 = 0.473684...) i rebutja
    errors típics que difereixen en almenys un mil·lèsim. El briefing
    original parlava de 1e-6, però a la pràctica els alumnes escriuen
    decimals truncats i 1e-6 retornaria `typical_error` per a
    aproximacions correctes.

    Retorna:
      True   — el valor coincideix dins la tolerància.
      False  — parseja però difereix.
      None   — no parseja. Delegueu a `L.judge_step`.

    `expected_value` pot ser un float (`0.038`) o un string (`"9/19"`).
    """
    if raw_text is None or expected_value is None:
        return None
    try:
        normalised = raw_text.strip().replace(",", ".").replace(" ", "")
        if not normalised:
            return None
        student = _Frac(normalised).limit_denominator(10_000)
        expected = _Frac(str(expected_value)).limit_denominator(10_000)
        return abs(float(student - expected)) < 1e-4
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def _check_set(raw_text: str, expected_value) -> "bool | None":
    """
    Comprova una llista de successos/elements (input_type = "set_listing").

    `expected_value` ha de ser un iterable d'strings. La comparació
    normalitza majúscules/minúscules i ignora claus, parèntesis i
    espais. Retorna None si l'input no sembla una llista (delega
    a la IA).

    Exemples acceptats: "{HH, HT, TH}", "HH HT TH", "hh, ht, th".
    """
    if raw_text is None or expected_value is None:
        return None
    try:
        cleaned = re.sub(r"[{}()\[\]]", " ", raw_text.lower())
        # Tokens separats per coma, punt-i-coma o espais.
        tokens = [t.strip() for t in re.split(r"[,;\s]+", cleaned) if t.strip()]
        if not tokens:
            return None
        student_set = set(tokens)
        expected_set = set(str(x).lower().strip() for x in expected_value)
        return student_set == expected_set
    except Exception:
        return None


# ============================================================
# Heurística determinista: l'input té contingut matemàtic?
# ============================================================
# Portada de `tutor-eq/verifier.has_math_content`, adaptada al domini de
# probabilitat. Detecta input "purament conversacional" (ex: "hola",
# "no ho sé", "què fa això") per disparar `_handle_inappropriate` abans
# de gastar una crida a la IA.
#
# Important: aquesta detecció és una PRIMERA línia. Si l'alumne escriu
# alguna cosa que sembla matemàtica però en realitat no diu res útil,
# `L.judge_step` ho atrapa al segon nivell (verdict typical_error o
# conceptual_gap).
_MATH_KEYWORDS_CA_EN = [
    "probabilitat", "probability", "p(", "prob",
    "favorable", "favorables", "favourable", "favorable",
    "casos", "cases", "total", "totals", "totales",
    "espai", "space", "mostral", "sample",
    "succés", "succes", "succesos", "event", "events", "esdeveniment",
    "complementari", "complement", "complementary",
    "condicional", "conditional", "donat", "given",
    "independent", "independents", "independents", "independencia",
    "bayes", "binomial", "laplace",
    "arbre", "tree", "rama", "branca", "branch",
    "sumar", "restar", "multiplicar", "dividir",
    "operació", "operation", "calcul", "compute",
    "fracció", "fraction", "decimal", "decimals",
]


def _has_math_content(text: str) -> bool:
    """
    Heurística: la cadena conté algun signe inequívocament matemàtic
    (dígit, operador, símbol de conjunt, paraula clau del domini)?

    Tornarà True per a respostes legítimes encara que siguin curtes
    ("3/8", "0.5", "P(A)", "Bayes"). Tornarà False per a "hola",
    "ajuda'm", "no ho sé sense pista".

    La comparació de paraules clau es fa sobre el text amb accents
    eliminats (NFD + remoure marques diacrítiques) per evitar que
    "càlcul" no coincideixi amb la keyword "calcul". Aquesta
    normalització és necessària perquè els alumnes de batxillerat
    escriuen amb accents (és la convenció ortogràfica catalana
    correcta) i no volem penalitzar-los per això.

    Usat per `_handle_inappropriate` com a guard pre-IA.
    """
    if not text:
        return False
    s = text.lower().strip()
    if not s:
        return False
    if re.search(r"[0-9]", s):
        return True
    # Operadors i símbols matemàtics o de conjunts.
    if any(c in s for c in ["+", "-", "*", "/", "=", "(", ")",
                            "{", "}", "∩", "∪", "|", "·", "×", "÷"]):
        return True
    # Strip d'accents per al match de keywords (NFD descompon els
    # caràcters accentuats; el filtre treu les marques diacrítiques).
    s_no_accents = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    return any(kw in s_no_accents for kw in _MATH_KEYWORDS_CA_EN)


# ============================================================
# Subflux: ús inadequat
# ============================================================
def _handle_inappropriate(state: dict, raw_text: str) -> dict:
    """
    Incrementa el comptador d'avisos d'ús inadequat. Al 3r avís
    (`MAX_INAPPROPRIATE_WARNINGS`), tanca la sessió amb veredicte
    `suspended`.

    Portat de `tutor-eq/tutor._handle_inappropriate`. Adaptat per:
    - escriure al `history` amb el mateix format que els torns
      normals (`type` = "inappropriate"), no amb el format de tutor-eq.
    - no resetejar `inappropriate_warnings` aquí (això es fa al
      `process_turn` quan torna a haver-hi contingut matemàtic).
    """
    state["inappropriate_warnings"] += 1
    n = state["inappropriate_warnings"]

    state["history"].append({
        "type":        "inappropriate",
        "step_id":     state["current_step_idx"] + 1,
        "student":     raw_text,
        "verdict":     "no_math",
        "error_label": None,
        "reason":      "",
        "ts":          time.time(),
    })

    if n >= MAX_INAPPROPRIATE_WARNINGS:
        state["verdict_final"] = "suspended"
        _push_msg(state, "warning",
                  "S'ha detectat un ús inadequat del sistema. "
                  "La sessió es tanca i el rastre queda registrat.")
        return state

    _push_msg(state, "warning",
              f"Avís {n}/{MAX_INAPPROPRIATE_WARNINGS}: la teva resposta "
              "no conté contingut matemàtic. Respon al pas amb el càlcul "
              "o el raonament que demana l'enunciat.")
    return state


# ============================================================
# Processar torn de prerequisit
# ============================================================
def _process_prereq_turn(state: dict, raw_text: str) -> dict:
    """
    Flux quan estem dins d'un mini-exercici de prereq (`active_prereq` !=
    None). L'avaluació és DETERMINISTA (no crida a la IA): comprova si la
    resposta conté almenys una keyword obligatòria i cap keyword
    prohibida.

    Aquest disseny és intencional: els prereqs són preguntes molt acotades
    (sí/no, una paraula clau...) i la IA no aporta res. A més és gratuït.
    """
    prereq = PB.get_prerequisite(state["active_prereq"])
    if prereq is None:
        # Defensiu: si l'id apunta a un prereq inexistent, ho desbloquegem
        # silenciosament. No hauria de passar si `problems.py` és coherent.
        state["active_prereq"] = None
        return state

    s_low = raw_text.lower()
    required = prereq.get("keywords_required", [])
    forbidden = prereq.get("forbidden_keywords", [])

    has_required = any(kw.lower() in s_low for kw in required)
    has_forbidden = any(kw.lower() in s_low for kw in forbidden)
    correct = has_required and not has_forbidden

    explanation = PB.get_localized(prereq.get("explanation", ""))
    prereq_id = prereq.get("id", state["active_prereq"])

    # Sortim del mode prereq sigui quin sigui el resultat: l'alumne ha vist
    # l'explicació i ja pot tornar a intentar l'original. Si encara no l'ha
    # entès, fallarà de nou i el sistema l'agafarà al següent torn.
    state["active_prereq"] = None
    state["active_prereq_depth"] = max(0, state["active_prereq_depth"] - 1)

    if correct:
        # `persistent=True`: el missatge sobreviu al reset del proper torn
        # perquè l'alumne segueixi tenint-lo a la vista mentre torna al
        # pas principal.
        _push_msg(
            state, "prereq_resolved",
            f"Exercici {prereq_id}: correcte. {explanation}\n\n"
            "**Ara, aplica el que has après al problema principal.**",
            persistent=True,
        )
        # Anotem el node del DAG com a "consolidat" per al rastre.
        dag_node = PB.DEPENDENCIES.get(
            prereq.get("concept", ""), {}
        ).get("dag_node")
        if dag_node and dag_node not in state["nodes_consolidated"]:
            state["nodes_consolidated"].append(dag_node)
    else:
        _push_msg(
            state, "prereq_failed",
            f"Exercici {prereq_id}: no és correcte. {explanation}\n\n"
            "**Continua intentant el problema principal.**",
            persistent=True,
        )
    return state


# ============================================================
# Gestionar buit conceptual
# ============================================================
def _handle_conceptual_gap(state: dict, step: dict, student_answer: str) -> dict:
    """
    La IA ha classificat la resposta com a `conceptual_gap`. Decideix què
    fer en funció de l'estat del retrocés i de la història de fallades.

    Lògica:
    1. Cridem `L.diagnose_dependency` per saber QUIN concepte falta.
    2. Si l'alumne JA mostra coneixement del concepte (keyword match) o
       JA ha fallat 2 cops aquest mateix concepte → pista socràtica via
       `L.generate_hint`. La intuïció: insistir amb un prereq que no ha
       resolt no aporta; cal canviar de tàctica.
    3. Altrament, retrocedeix a un mini-exercici de prereq, sempre que no
       hàgim arribat ja al límit de profunditat.
    4. Si arribem a `MAX_BACKTRACK_DEPTH`, marquem `referred_to_tutor`:
       el sistema reconeix que l'alumne necessita més suport del que pot
       oferir.
    """
    dep_id = L.diagnose_dependency(step, student_answer, state["problem"])
    if dep_id is None:
        # Edge case: la IA no ha sabut identificar la dependència. Donem
        # un missatge genèric i no fem retrocés (millor un fals negatiu
        # que enviar l'alumne a un prereq aleatori).
        _push_msg(state, "feedback",
                  "Hi ha un buit conceptual. Repassa les definicions bàsiques.")
        return state

    already_knows = _quick_keyword_check(dep_id, student_answer)
    streak = state["concept_failure_streak"].get(dep_id, 0) + 1
    state["concept_failure_streak"][dep_id] = streak

    # Heurística de canvi de tàctica: pista socràtica si ja sap el
    # concepte (només falla en l'aplicació) o si ha fallat repetidament.
    if already_knows or streak >= 2:
        try:
            hint = L.generate_hint(step, dep_id)
            _push_msg(state, "hint", f"Pista: {hint}")
        except Exception as e:
            _push_msg(state, "warning", f"Error de connexió amb la IA: {e}")
        return state

    # Retrocés a prerequisit. Comprovem el sostre primer per evitar bucles.
    if state["backtrack_depth"] >= MAX_BACKTRACK_DEPTH:
        _push_msg(state, "warning",
                  "Sembla que necessites més suport del que aquesta sessió pot oferir. "
                  "Et recomanem assistir a una tutoria presencial.")
        state["verdict_final"] = "referred_to_tutor"
        return state

    dep = PB.get_dependency(dep_id)
    if dep is None:
        # Edge case: la IA ha retornat un dep_id que no existeix.
        # Defensiu: silenci.
        return state

    prereq_id = dep.get("prerequisite")
    prereq = PB.get_prerequisite(prereq_id)
    if prereq is None:
        return state

    # Activem el mode prereq. Els propers torns aniran a
    # `_process_prereq_turn` fins que l'alumne respongui.
    state["active_prereq"] = prereq_id
    state["active_prereq_depth"] = state["backtrack_depth"] + 1
    state["backtrack_depth"] += 1
    state["backtrack_count"] += 1
    dep_desc = PB.get_localized(dep["description"])
    prereq_q = PB.get_localized(prereq["question"])
    _push_msg(state, "prereq",
              f"Cal consolidar abans un concepte: **{dep_desc}**.\n\n"
              f"**Exercici de reforç:** {prereq_q}")
    return state


# ============================================================
# Finalitzar sessió si tots els passos estan completats
# ============================================================
def _maybe_finish(state: dict) -> dict:
    """Marca la sessió com a resolta si ja no queden passos."""
    problem = state["problem"]
    if state["current_step_idx"] >= len(problem["passos"]):
        state["verdict_final"] = "solved"
        _push_msg(state, "system",
                  "✓ Problema completat correctament. Molt bé!")
    return state


# ============================================================
# Processar torn principal
# ============================================================
def process_turn(state: dict, raw_input: str) -> dict:
    """
    Punt d'entrada únic. Modifica l'estat in-place i el retorna.

    Flux:
      1. Estem dins d'una sub-sessió de prereq → `_process_prereq_turn`.
      2. Input sense contingut matemàtic → `_handle_inappropriate`.
      3. Despatx per `step["input_type"]`:
         - `integer`/`decimal`/`fraction`/`set_listing` → verificador
           determinista, amb fallback a `L.judge_step` si l'input no
           parseja com a número.
         - `free_text` (i qualsevol altre valor) → `L.judge_step` directe.

    No hi ha senyals especials (`!!`, `?`, `!text`). L'alumne sempre
    envia text que respon al pas. Pistes i retrocés a prerequisits
    són exclusivament proactius, gestionats internament per
    `_handle_conceptual_gap`.

    NOTA: fem `copy.deepcopy` perquè Streamlit pot rerunear arbitràriament
    i no volem que muteu l'estat anterior si això es queda penjat per
    qualsevol motiu.
    """
    state = copy.deepcopy(state)

    # Neteja missatges no persistents al començament de cada torn. Els
    # marcats com a `persistent=True` (típicament `prereq_resolved` i
    # `prereq_failed`) es conserven perquè l'alumne pugui seguir veient
    # el feedback del retrocés mentre torna al problema principal.
    state["messages"] = [m for m in state["messages"] if m.get("persistent")]

    s = (raw_input or "").strip()

    # --- Sessió de prerequisit activa ---
    # Tots els torns dins d'un mini-exercici de reforç van per aquí, NO
    # pel flux normal d'avaluació de pas.
    if state["active_prereq"] is not None:
        return _process_prereq_turn(state, s)

    # --- Detecció determinista d'ús inadequat ---
    # Si l'input no conté cap senyal de contingut matemàtic, NO gastem
    # una crida a la IA: marquem un avís i, al 3r, suspenem la sessió.
    # Aquesta heurística és la primera línia; la IA pot atrapar al
    # segon nivell respostes que semblen matemàtiques però buides.
    if not _has_math_content(s):
        return _handle_inappropriate(state, s)
    # Si torna a haver-hi contingut matemàtic, oblidem avisos previs:
    # l'alumne ha tornat al carril correcte.
    state["inappropriate_warnings"] = 0

    # --- Pas normal de resolució ---
    problem = state["problem"]
    steps = problem["passos"]

    if state["current_step_idx"] >= len(steps):
        # Defensiu: ja s'havia acabat. No hauria de passar perquè
        # _maybe_finish marca `verdict_final` i app.py atura el render.
        state["verdict_final"] = "solved"
        return state

    step = steps[state["current_step_idx"]]

    # Despatx per tipus d'input. Els tipus deterministes (`integer`,
    # `decimal`, `fraction`, `set_listing`) comproven primer amb un
    # verificador local; si l'input no parseja, deleguen a `judge_step`
    # com a fallback (l'alumne pot haver escrit raonament en text en
    # comptes d'un número).
    #
    # Per a `free_text` (i qualsevol valor desconegut) anem directament
    # a la IA, que és el comportament heretat de tutor-grups.
    input_type = step.get("input_type", "free_text")
    reason = ""
    # Camps només significatius quan verdict == "incomplete". Es queden
    # a None per als veredictes deterministes i per als altres veredictes
    # de la IA. Els recollim aquí, fora del try, per simplificar la branca
    # de `incomplete` posterior.
    next_question = None
    missing = None
    # Snapshot defensiu: passem una CÒPIA de les parcials, no la referència.
    # Si no en fèssim còpia, l'`append` posterior a `state["step_partials"]`
    # mutaria també l'objecte que el judge ja ha rebut (i el que el mock
    # framework ha guardat a `call_args`, cosa que fa fallar tests).
    partials = list(state["step_partials"]) if state.get("step_partials") else None
    try:
        if input_type == "integer":
            result = _check_integer(s, step.get("expected_value"))
            if result is True:
                verdict, error_label = "correct", None
            elif result is False:
                verdict = "typical_error"
                error_label = step.get("typical_error_label")
            else:
                judgment = L.judge_step(step, s, partials)
                verdict = judgment["verdict"]
                reason = judgment.get("reason", "")
                error_label = judgment.get("error_label")
                next_question = judgment.get("next_question")
                missing = judgment.get("missing")

        elif input_type in ("decimal", "fraction"):
            result = _check_numeric(s, step.get("expected_value"))
            if result is True:
                verdict, error_label = "correct", None
            elif result is False:
                verdict = "typical_error"
                error_label = step.get("typical_error_label")
            else:
                # Input no parsejable: pot ser raonament o un format
                # inusual. Delegueu a la IA.
                judgment = L.judge_step(step, s, partials)
                verdict = judgment["verdict"]
                reason = judgment.get("reason", "")
                error_label = judgment.get("error_label")
                next_question = judgment.get("next_question")
                missing = judgment.get("missing")

        elif input_type == "set_listing":
            result = _check_set(s, step.get("expected_value"))
            if result is True:
                verdict, error_label = "correct", None
            elif result is False:
                verdict = "typical_error"
                error_label = step.get("typical_error_label")
            else:
                judgment = L.judge_step(step, s, partials)
                verdict = judgment["verdict"]
                reason = judgment.get("reason", "")
                error_label = judgment.get("error_label")
                next_question = judgment.get("next_question")
                missing = judgment.get("missing")

        else:
            # input_type == "free_text" o qualsevol altra cosa: IA.
            # Aquesta és l'ÚNICA crida del codi viu sense plan B
            # determinista. Si la IA està caiguda, l'alumne queda
            # bloquejat al pas actual. (Els retries automàtics de
            # `llm._call_with_retry` cobreixen errors transitoris; els
            # no-transitoris els capturem aquí.)
            judgment = L.judge_step(step, s, partials)
            verdict = judgment["verdict"]
            reason = judgment.get("reason", "")
            error_label = judgment.get("error_label")
            next_question = judgment.get("next_question")
            missing = judgment.get("missing")
    except Exception as e:
        _push_msg(state, "warning", f"Error de connexió amb la IA: {e}")
        return state

    # Registrem el torn al rastre abans de decidir què fer (així queda
    # constància encara que la lògica posterior tingui un bug).
    turn = {
        "type":        "step",
        "step_id":     step["id"],
        "student":     s,
        "verdict":     verdict,
        "error_label": error_label,
        "reason":      reason,
        "ts":          time.time(),
    }
    if verdict == "incomplete":
        # Camps específics d'`incomplete`: útils al rastre del professor
        # per veure on s'ha partit la conversa socràticament.
        turn["missing"] = missing
        turn["next_question"] = next_question
    state["history"].append(turn)

    if verdict == "correct":
        # Pas superat: avancem i resetegem comptadors d'estancament.
        # `concept_failure_streak` es reseteja sencer (no només la clau
        # del concepte actual) perquè un pas correcte indica que l'alumne
        # està en bona forma; no té sentit arrossegar streaks antigues.
        # `step_partials` també es reseteja: les parcials del pas anterior
        # no valen per al següent.
        state["stagnation_consecutive"] = 0
        state["concept_failure_streak"] = {}
        state["step_partials"] = []
        _push_msg(state, "feedback",
                  f"✓ Correcte. {reason}".strip())
        state["current_step_idx"] += 1
        return _maybe_finish(state)

    if verdict == "incomplete":
        # L'alumne ha respost bé però parcialment. Acumulem la resposta a
        # `step_partials` perquè el judge del proper torn pugui validar la
        # unió cumulativa. NO avancem el pas, NO incrementem stagnation, NO
        # disparem `_handle_conceptual_gap`. Una conversa socràtica de
        # diversos torns sobre el mateix pas és sana, no és bloqueig: el
        # pas pot demanar 4 coses i l'alumne respondre-les en 2 o 3 torns.
        state["step_partials"].append(s)
        nq = (next_question or "").strip()
        ack = (reason or "").strip()
        if ack and nq:
            text = f"{ack} {nq}"
        else:
            text = nq or ack or "Vas bé però et falta algun element. Continua amb el pas."
        _push_msg(state, "feedback", text)
        return state

    # --- Gestió d'errors ---
    state["stagnation_consecutive"] += 1

    if verdict == "conceptual_gap":
        # Mostrem el motiu donat per la IA com a feedback abans del retrocés
        # (orientativament: "no estàs aplicant la definició de probabilitat
        # condicionada").
        if reason:
            _push_msg(state, "feedback", reason)
        return _handle_conceptual_gap(state, step, s)

    # verdict == "typical_error" (o qualsevol cosa inesperada):
    # mostrem el missatge del catàleg si en tenim, o si no, el motiu donat
    # per la IA, o un missatge genèric.
    raw_cat = PB.ERROR_CATALOG.get(error_label or "", "")
    msg = PB.get_localized(raw_cat) if raw_cat else (
        reason or "La resposta no és correcta. Revisa el pas."
    )
    _push_msg(state, "feedback", msg)
    return state


# ============================================================
# Rastre JSON per al professor
# ============================================================
def build_trace(state: dict) -> dict:
    """
    Genera el rastre serialitzable per al professor al final d'una sessió.

    Camps clau:
    - `torns`: la història completa de classificacions de la IA. Permet
      revisió posterior dels falsos positius i negatius del classificador.
    - `pistes`: nº de pistes proactives generades per l'engine quan
      `_handle_conceptual_gap` ha detectat que l'alumne ja coneix el
      concepte. (No hi ha pistes a petició: l'alumne no pot demanar-les.)
    - `retrocessos`: comptador de mini-exercicis de reforç activats.
      Indica buits conceptuals.
    - `nodes_consolidats`: nodes del DAG que l'alumne ha consolidat
      durant la sessió. Útil per analítica a nivell d'alumne (què sap
      ara que abans no) un cop tinguem múltiples sessions per alumne.
    - `avisos_us_inadequat`: nº d'avisos pre-IA per input sense
      contingut matemàtic.

    No incloem cap identificador de l'alumne (la sessió és anònima);
    el `session_id` és suficient per correlacionar torns amb el log
    de l'API.
    """
    duration = time.time() - state["started_at_ts"]
    problem = state["problem"]
    # Comptem les pistes que apareixen al `messages` ja gravades a
    # `history` indirectament: en aquest moment, l'únic camí que pot
    # afegir-les és `_handle_conceptual_gap` → `L.generate_hint` →
    # `_push_msg(kind="hint")`. No es queden al history, així que el
    # comptador segueix derivat del fluxe de buit-conceptual al history.
    n_hints = sum(
        1 for t in state["history"]
        if t.get("type") == "step" and t.get("verdict") == "conceptual_gap"
    )
    # Comptador de re-preguntes socràtiques per resposta parcial correcta.
    # És una mètrica útil per al professor: si un pas concret acumula molts
    # `incomplete_followups`, segurament està mal calibrat (demana massa
    # coses alhora) i caldria partir-lo en passos més atòmics.
    n_incomplete = sum(
        1 for t in state["history"]
        if t.get("type") == "step" and t.get("verdict") == "incomplete"
    )
    return {
        "session_id":  state["session_id"],
        "problema": {
            "id":     state["problem_id"],
            "node":   problem.get("node"),
            "familia": problem.get("familia"),
            "nivell": problem.get("nivell"),
            "tema":   problem.get("tema"),
            "enunciat": problem.get("enunciat"),
        },
        "started_at":    state["started_at"],
        "durada_segons": round(duration, 1),
        "passos_totals": len(problem["passos"]),
        "pas_actual":    state["current_step_idx"],
        "torns":         state["history"],
        "pistes": {
            # Comptador de pistes proactives. No hi ha "posicions" perquè
            # les pistes proactives es generen al moment del buit conceptual
            # i ja queden gravades a `torns` (com a torn amb verdict
            # `conceptual_gap`).
            "total": n_hints,
        },
        "retrocessos": {
            "total":         state["backtrack_count"],
            "profunditat":   state["backtrack_depth"],
        },
        "incomplete_followups": n_incomplete,
        "nodes_consolidats":   state["nodes_consolidated"],
        "avisos_us_inadequat": state.get("inappropriate_warnings", 0),
        "veredicte_final":     state["verdict_final"] or "en_curs",
    }


def serialize_trace(state: dict) -> str:
    """Versió string del rastre, llesta per a `open(...).write()` o `st.json`."""
    return json.dumps(build_trace(state), ensure_ascii=False, indent=2)


# ============================================================
# Mode debug: test exhaustiu del flux end-to-end (CAPA B)
# ============================================================
# Adaptat de `tutor-eq/tutor.py::run_exhaustive_test`. Diferències clau
# del nostre context:
#
#  - Tutor-eq tenia un model "una equació, baseline que avança per
#    reescriptura". Nosaltres tenim "passos atòmics, baseline que
#    avança per `current_step_idx`". Una "ronda" és un PAS del problema.
#  - Tenim un veredicte extra: `incomplete`. Aquest NO avança el pas
#    però tampoc és error: si el test l'espera, és match exacte.
#  - Els nostres passos tenen verifiers deterministes (decimal /
#    fraction / integer / set_listing). Per a aquests, els inputs
#    parsejables NO criden la IA → cost API zero per a aquesta ronda.
#    Útil igualment com a regressió de `_check_*`.
#
# Política d'aïllament del logging
# --------------------------------
# Reescrivim el context del thread a (student_id='__test_exhaustiu__',
# session_id=<id efímer>) perquè el cost d'aquest test no es barregi
# amb les analítiques de cap alumne real. El context anterior es
# restaura al final via try/finally. Idèntic patró que tutor-eq.
def run_exhaustive_test(problem_id: str, on_progress=None,
                        session_id: str = None) -> list:
    """
    Executa la bateria de rondes de prova per a un problema, una ronda
    per pas. Cada input del guió s'executa contra una CÒPIA del baseline,
    per garantir que els tests són independents entre ells (un error en
    un input no contamina els altres).

    `on_progress(round_idx, n_rounds, item_idx, n_items)`: callback
    opcional per a la UI; les excepcions del callback s'ignoren perquè
    no han de poder tombar la bateria.

    `session_id`: id per al logging. Si no se'n passa, se'n genera un.
    El caller pot fer després `api_logger.summarize_session(sid)` per
    saber el cost total d'aquesta execució.

    Retorna una llista de dicts (una entrada per ronda) amb la forma:
        {
          "round":      int,       # 1-based
          "step_id":    int | str, # del pas corresponent
          "step_text":  str,       # text del pas (per al rastre humà)
          "from_step_idx": int,    # current_step_idx del baseline a l'inici
          "items": [
            {
              "input":               str,
              "expected":            str,           # del schema TEST_CASES
              "expected_error_label": str | None,
              "rationale":           str,
              "verdict":             str | None,    # gravat al history
              "error_label":         str | None,
              "feedback":            str,           # 1r missatge `feedback`
              "missing":             str | None,    # només si verdict=="incomplete"
              "next_question":       str | None,
              "active_prereq":       str | None,
              "match":               bool,
              "exception":           str | None,
            },
            ...
          ]
        }

    Nota d'errors
    -------------
    Les excepcions d'una crida individual a `process_turn` es capturen
    al camp `exception` de l'item; no aturen el lot. Una excepció
    catastròfica fora del bucle (per exemple, `problem_id` invàlid)
    sí que es propaga al caller — és el que fa el lot 1-for-all per
    comptar "errors_api" per problema.
    """
    rounds = PB.get_test_cases(problem_id)
    if not rounds:
        return []

    test_sid = session_id or uuid.uuid4().hex[:12]
    _prev_student, _prev_session = L.get_log_context()
    L.set_log_context(student_id="__test_exhaustiu__", session_id=test_sid)
    try:
        return _run_exhaustive_test_inner(rounds, problem_id, on_progress, test_sid)
    finally:
        L.set_log_context(student_id=_prev_student, session_id=_prev_session)


def _run_exhaustive_test_inner(rounds, problem_id, on_progress, test_sid):
    # OJO subtilesa de threading: `new_session_state` crida internament
    # `L.set_log_context(student_id=None, session_id=<uuid_nou>)`. Això
    # SOBREESCRIU el context que el wrapper acaba de fixar. Sense
    # restaurar-lo, totes les crides reals a `judge_step` durant el
    # test es loguen sota l'uuid aleatori, no sota `test_sid`. Resultat:
    # `summarize_session(test_sid)` torna 0 crides. Per això fixem el
    # context EN AQUEST punt — i a cada `process_turn` no cal tornar-ho
    # a fer perquè `process_turn` NO el toca.
    baseline = new_session_state(problem_id)
    L.set_log_context(student_id="__test_exhaustiu__", session_id=test_sid)
    all_results = []

    for round_idx, round_items in enumerate(rounds, start=1):
        if not round_items:
            # Ronda buida al schema: la deixem registrada però sense items.
            all_results.append({
                "round":         round_idx,
                "step_id":       None,
                "step_text":     "",
                "from_step_idx": baseline["current_step_idx"],
                "items":         [],
            })
            continue

        # Sanity check del schema: el primer item ha de ser correct.
        # Si no, advertim al rastre però NO petem (potser és intencional
        # per testos de regressió en què el correct no existeix encara).
        if round_items[0].get("expected") != "correct":
            schema_warning = (
                f"Atenció: el primer item de la ronda {round_idx} té "
                f"`expected={round_items[0].get('expected')!r}`, no `correct`. "
                f"El baseline NO avançarà i les rondes següents es "
                f"resoldran contra l'estat actual."
            )
        else:
            schema_warning = None

        step = baseline["problem"]["passos"][baseline["current_step_idx"]]
        round_data = {
            "round":         round_idx,
            "step_id":       step.get("id"),
            "step_text":     PB.get_localized(step.get("text", "")),
            "from_step_idx": baseline["current_step_idx"],
            "items":         [],
        }
        if schema_warning:
            round_data["schema_warning"] = schema_warning

        for item_idx, item_spec in enumerate(round_items):
            if on_progress is not None:
                try:
                    on_progress(round_idx, len(rounds),
                                item_idx + 1, len(round_items))
                except Exception:
                    pass

            raw = item_spec.get("input", "")
            expected = item_spec.get("expected", "correct")
            expected_label = item_spec.get("expected_error_label")

            item = {
                "input":                raw,
                "expected":             expected,
                "expected_error_label": expected_label,
                "rationale":            item_spec.get("rationale", ""),
                "verdict":              None,
                "error_label":          None,
                "feedback":             "",
                "missing":              None,
                "next_question":        None,
                "active_prereq":        None,
                "match":                False,
                "exception":            None,
            }
            try:
                # IMPORTANT: el nostre process_turn fa `state = copy.deepcopy(state)`
                # al començament i retorna el nou estat (NO muta in-place,
                # a diferència de `tutor-eq`). Cal capturar el valor retornat.
                test_state = process_turn(copy.deepcopy(baseline), raw)
            except Exception as e:
                # Capturem TIPUS + missatge — el tipus sol és poc útil,
                # i el repr complet és massa sorollós.
                item["exception"] = f"{type(e).__name__}: {e}"
                round_data["items"].append(item)
                continue

            # Extreure la informació rellevant de l'estat resultant.
            history_steps = [h for h in test_state.get("history", [])
                             if h.get("type") == "step"]
            last_step = history_steps[-1] if history_steps else {}
            item["verdict"]       = last_step.get("verdict")
            item["error_label"]   = last_step.get("error_label")
            item["missing"]       = last_step.get("missing")
            item["next_question"] = last_step.get("next_question")
            item["active_prereq"] = test_state.get("active_prereq")
            feedbacks = [m["text"] for m in test_state.get("messages", [])
                         if m.get("kind") == "feedback"]
            item["feedback"] = feedbacks[0] if feedbacks else ""

            # Match: verdict ha de coincidir exactament. Si el guió
            # especifica `expected_error_label`, també ha de coincidir
            # (només té sentit quan expected ∈ {typical_error, conceptual_gap}).
            verdict_match = (item["verdict"] == expected)
            label_match = (
                expected_label is None
                or item["error_label"] == expected_label
            )
            item["match"] = verdict_match and label_match

            round_data["items"].append(item)

        all_results.append(round_data)

        # Avançar el baseline amb el primer input (que hauria de ser
        # `correct`). Si NO avança, trenquem el lot — els passos
        # posteriors es resoldrien des d'un estat que no correspon.
        try:
            first_input = round_items[0].get("input", "")
            # Re-assignem el baseline al retorn (process_turn NO muta in-place).
            baseline = process_turn(baseline, first_input)
            if baseline["current_step_idx"] == round_data["from_step_idx"]:
                # El baseline NO ha avançat. Pot ser:
                #  - el guió té un error (primer item no és realment correct),
                #  - la IA està caiguda i el typical_error fallback s'ha disparat,
                #  - o el verificador determinista ha refusat l'input.
                # Sigui com sigui, no té sentit continuar.
                all_results.append({
                    "round":         round_idx + 1,
                    "step_id":       None,
                    "step_text":     "",
                    "from_step_idx": baseline["current_step_idx"],
                    "items":         [],
                    "schema_warning": (
                        f"Baseline no avançat després de la ronda {round_idx}. "
                        f"Test interromput; les rondes >= {round_idx + 1} no "
                        f"s'executen. Reviseu el primer item de la ronda {round_idx}."
                    ),
                })
                break
        except Exception:
            break

    return all_results
