"""
Client a l'API de Google Gemini.

Visió general per a un lector nou
=================================
Aquest mòdul aïlla tota la dependència amb Google Gemini. La resta del
codi mai crida la SDK directament; sempre passa per aquestes 3 funcions:

1. `judge_step(step, student_answer, step_partials=None)` — avalua un pas
   de resolució de l'alumne. Retorna un veredicte categòric (`correct` /
   `incomplete` / `typical_error` / `conceptual_gap`) + raó + etiqueta +
   (per a `incomplete`) `missing` i `next_question`. Les respostes
   anteriors de l'alumne dins del mateix pas (`step_partials`) es
   consideren per validar la unió cumulativa: un pas que demana N coses
   pot tancar-se amb N torns parcials.

2. `diagnose_dependency(step, student_answer, problem)` — quan
   `judge_step` ha marcat `conceptual_gap`, aquesta segona crida
   identifica QUINA de les dependències del problema falta.

3. `generate_hint(step, dep_id)` — pista socràtica curta. Text lliure
   (no JSON), màxim 2 frases, sense LaTeX.

Tot el sistema treballa en català. Els prompts del sistema estan en
català, i `_loc` (que llegeix camps bilingües de `problems.py`) sempre
retorna l'entrada catalana.

Quina és la diferència amb `tutor-eq/llm.py`?
---------------------------------------------
A `tutor-eq` les crides són 4 i el flux és més sofisticat perquè SymPy
ja ha decidit l'equivalència matemàtica abans d'arribar a la IA. Aquí
SymPy no existeix com a font de veritat: per als passos numèrics
(`decimal`, `fraction`, `integer`) la comparació la fa
`tutor._check_numeric` amb `Fraction`; per als passos `free_text` el
judici el fa `judge_step`. Si la comparació numèrica no parseja
l'input, també es delega a `judge_step` com a fallback.

Robustesa
---------
- Retry amb exponential backoff (3 intents, base 1.5s → 3s → 6s) per a
  errors transitoris: 503/UNAVAILABLE, 429/RATE_LIMIT, 500/INTERNAL,
  DEADLINE_EXCEEDED i timeouts.
- Tota crida queda gravada al log (`api_logger.py`) amb tokens, cost USD
  estimat, durada i `student_id`/`session_id` del context actual.

Models i thinking budget
------------------------
El thinking model (`gemini-2.5-pro`) cobra els tokens "thoughts" com a
output i pot esgotar el `max_output_tokens` sense generar text visible.
Per això, quan el model és thinking, multipliquem el sostre per
`TOKEN_MULTIPLIER = 10`. Per a models sense thinking (`flash`,
`flash-lite`), desactivem explícitament el thinking budget per estalviar
cost i latència.

Variables d'entorn:
- GEMINI_API_KEY (obligatori)
- GEMINI_MODEL (opcional, default `gemini-2.5-flash`)
"""

import json
import os
import re
import time
import threading
import uuid

import api_logger
from problems import DEPENDENCIES, get_localized as _loc

# La importació de la SDK es fa try/except perquè volem que `import llm`
# funcioni encara que no estigui instal·lada (útil per a tests amb mocks
# i per a `tutor.py` quan només es vol exercitar lògica sense API).
try:
    from google import genai
    from google.genai import types as _genai_types
except ImportError:
    genai = None
    _genai_types = None

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_TOKENS = 400

# Detectem el thinking model per nom (el SDK no exposa una propietat
# `is_thinking`). Si Google introdueix un nou model thinking amb un nom
# diferent, cal actualitzar aquesta heurística.
IS_THINKING_MODEL = "pro" in MODEL.lower()
# Els thinking tokens es facturen com a output i poden consumir el sostre
# sense que el model emeti text visible. Multipliquem per 10 quan és
# thinking. (Empíric; ajustar al pilot si veiem respostes truncades.)
TOKEN_MULTIPLIER = 10 if IS_THINKING_MODEL else 1

# Retry config per a errors transitoris de l'API.
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 1.5

# Patrons d'error que considerem RETRIABLES. Tota la resta (4xx no-rate,
# 401 unauthorized, etc.) es propaga immediatament sense reintentar.
RETRIABLE_PATTERNS = (
    "503", "UNAVAILABLE",
    "429", "RATE_LIMIT", "RESOURCE_EXHAUSTED",
    "500", "INTERNAL",
    "DEADLINE_EXCEEDED",
    "timeout", "Timeout",
)

# ============================================================
# Context de logging (thread-local)
# ============================================================
# Streamlit pot servir múltiples usuaris simultanis al mateix procés
# Python. Si guardem `student_id`/`session_id` com a globals, els logs
# d'usuaris diferents es barregen. Per evitar-ho, l'estat de context viu
# en `threading.local`. Cada request de Streamlit té el seu thread (en
# general) i, per tant, el seu context.
#
# `_PROCESS_FALLBACK_SESSION` és un id estable per procés que s'usa si
# encara no s'ha cridat `set_log_context` (situació típica: testos
# manuals des d'un script). No s'ha d'usar en producció.
_PROCESS_FALLBACK_SESSION = uuid.uuid4().hex[:8]
_log_ctx = threading.local()


def set_log_context(student_id: str, session_id: str):
    """
    Fixa el context de logging per al thread actual. Subseqüents crides a
    l'API quedaran marcades amb aquest `student_id` i `session_id` als
    logs (`api_calls_YYYY-MM-DD.jsonl`).

    Es crida típicament des de `tutor.new_session_state()` (que fixa
    ambdós camps) i defensivament des d'`app.py` a cada rerun de
    Streamlit (per si l'estat es perd).
    """
    _log_ctx.student_id = student_id
    _log_ctx.session_id = session_id


def _current_session_id() -> str:
    return getattr(_log_ctx, "session_id", None) or _PROCESS_FALLBACK_SESSION


def _current_student_id() -> str:
    return getattr(_log_ctx, "student_id", None) or "anon"


def get_log_context() -> tuple:
    """Retorna (student_id, session_id) actuals sense fallbacks.
    Útil per a guardar/restaurar context (p.ex. tests aïllats)."""
    return (
        getattr(_log_ctx, "student_id", None),
        getattr(_log_ctx, "session_id", None),
    )


def get_session_id() -> str:
    """Retorna el `session_id` actual del thread, amb fallback de procés.
    `app.py` la usa per filtrar `api_logger.summarize_session`."""
    return _current_session_id()


# Callback opcional per a missatges de progrés durant els retries.
# Si es defineix (típicament des de la UI), s'invoca abans de cada sleep
# de backoff per informar l'usuari ("L'API està lenta, esperant 3s...").
_progress_callback = None


def set_progress_callback(callback):
    global _progress_callback
    _progress_callback = callback


def _notify(msg: str):
    if _progress_callback is not None:
        try:
            _progress_callback(msg)
        except Exception:
            # Mai propaguem una excepció del callback; només volíem
            # informar i no és crític.
            pass


def _is_retriable(err: Exception) -> bool:
    s = str(err)
    return any(p in s for p in RETRIABLE_PATTERNS)


# ============================================================
# Client Gemini (lazy)
# ============================================================
_client = None


def _get_client():
    """Singleton lazy: només s'instancia el client al primer ús real.
    Així `import llm` no falla si l'API_KEY no està definida (útil per
    a tests amb mocks)."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    return _client


def _build_config(system: str, max_tokens: int, json_mode: bool, temperature: float):
    """Munta el `GenerateContentConfig`. La idea és aïllar aquí tots els
    detalls de la SDK (system instruction, response_mime_type, thinking
    budget) perquè les funcions de prompt no en sàpiguen res."""
    types = _genai_types
    cfg = {
        "system_instruction": system,
        # Apliquem el multiplicador per a thinking models: vegeu comentari
        # a la declaració de TOKEN_MULTIPLIER.
        "max_output_tokens": max_tokens * TOKEN_MULTIPLIER,
        "temperature": temperature,
    }
    if json_mode:
        # Força sortida en JSON. Reduïm així la freqüència de respostes
        # amb prefixos ```json o text explicatiu davant del JSON.
        cfg["response_mime_type"] = "application/json"
    if not IS_THINKING_MODEL:
        # Pels models sense thinking, desactivem-lo explícitament (no
        # se cobra però els reduiria latència per cap benefici).
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    return types.GenerateContentConfig(**cfg)


def _do_call(system: str, user: str, max_tokens: int,
             json_mode: bool, temperature: float):
    """Crida única (un sol intent) i retorna (text, tokens). Llança
    excepció en qualsevol error. El `_call_with_retry` és qui gestiona
    els intents múltiples."""
    client = _get_client()
    config = _build_config(system, max_tokens, json_mode, temperature)
    response = client.models.generate_content(
        model=MODEL, contents=user, config=config,
    )
    text = response.text or ""
    if not text.strip():
        # Cas típic dels thinking models: tot el budget se'n va en thoughts
        # i no queda res per a la resposta visible. Llancem amb un missatge
        # informatiu (inclou `finish_reason` per al log).
        finish = "?"
        if response.candidates:
            finish = getattr(response.candidates[0], "finish_reason", "?")
        raise RuntimeError(
            f"Resposta buida del model {MODEL} (finish_reason={finish})."
        )
    # Extreure tokens si el SDK els ha proporcionat. Tots tres camps poden
    # ser None segons el model i la versió de la SDK; defensem-nos amb 0s.
    usage = getattr(response, "usage_metadata", None)
    tokens = None
    if usage is not None:
        tokens = {
            "input":    int(getattr(usage, "prompt_token_count", 0) or 0),
            "output":   int(getattr(usage, "candidates_token_count", 0) or 0),
            "thoughts": int(getattr(usage, "thoughts_token_count", 0) or 0),
            "total":    int(getattr(usage, "total_token_count", 0) or 0),
        }
    return text, tokens


def _call_with_retry(function_name: str, system: str, user: str,
                     max_tokens: int, json_mode: bool,
                     temperature: float) -> str:
    """
    Embolcall que afegeix:
    - Reintents amb backoff exponencial per a errors transitoris.
    - Logging unificat (èxit i fracàs es registren a `api_logger`).

    `function_name` és el nom de la crida d'alt nivell (`judge_step`,
    `diagnose_dependency`, `generate_hint`) i s'usa al log per agregar
    per funció.
    """
    last_error = None
    # Truncem el system instruction al log perquè és llarg i repetitiu;
    # 200 chars és suficient per a identificar quin prompt s'ha usat.
    input_data = {
        "system_preview": system[:200],
        "user": user,
        "max_tokens": max_tokens,
        "json_mode": json_mode,
        "temperature": temperature,
    }

    for attempt in range(1, MAX_ATTEMPTS + 1):
        t0 = time.time()
        try:
            text, tokens = _do_call(system, user, max_tokens, json_mode, temperature)
            elapsed = time.time() - t0
            api_logger.log_call(
                session_id=_current_session_id(),
                student_id=_current_student_id(),
                function=function_name,
                model=MODEL, attempt=attempt, ok=True,
                elapsed_s=elapsed, input_data=input_data,
                output_data={"text_preview": text[:500], "len": len(text)},
                tokens=tokens,
            )
            return text
        except Exception as e:
            elapsed = time.time() - t0
            err_str = str(e)
            # Loggegem TAMBÉ els intents fallits, perquè volem comptar-los
            # al cost (no han costat tokens, però sí temps i un slot de
            # rate-limit).
            api_logger.log_call(
                session_id=_current_session_id(),
                student_id=_current_student_id(),
                function=function_name,
                model=MODEL, attempt=attempt, ok=False,
                elapsed_s=elapsed, input_data=input_data,
                error=err_str,
            )
            last_error = e
            # Decidim si reintentem. Errors no-retriables (p.ex. 401 unauth,
            # 400 bad request) es propaguen immediatament.
            if attempt < MAX_ATTEMPTS and _is_retriable(e):
                backoff = BACKOFF_BASE_S * (2 ** (attempt - 1))
                _notify(
                    f"L'API ha donat un error temporal (intent {attempt}/{MAX_ATTEMPTS}). "
                    f"Reintentant en {backoff:.0f}s..."
                )
                time.sleep(backoff)
                continue
            break

    raise RuntimeError(
        f"L'API ha fallat després de {MAX_ATTEMPTS} intents. Últim error: {last_error}"
    )


def _call_json(system: str, user: str, max_tokens: int = MAX_TOKENS,
               function_name: str = "unknown") -> str:
    """Wrapper de `_call_with_retry` per a respostes en JSON estricte.
    Temperature baixa (0.2): volem judicis estables, no creativitat."""
    return _call_with_retry(function_name, system, user, max_tokens,
                            json_mode=True, temperature=0.2)


def _call_text(system: str, user: str, max_tokens: int = MAX_TOKENS,
               function_name: str = "unknown") -> str:
    """Wrapper per a respostes en text lliure (pistes).
    Temperature una mica més alta (0.4): permet variar formulacions."""
    return _call_with_retry(function_name, system, user, max_tokens,
                            json_mode=False, temperature=0.4)


def _extract_json(text: str) -> dict:
    """
    Parsing defensiu de JSON. Tot i que demanem `response_mime_type =
    application/json`, els models ocasionalment encara emboliquen amb
    ```json``` o afegeixen text al començament/final. Estratègia:

    1. Treu fences de markdown.
    2. Intenta `json.loads` directament.
    3. Si falla, cerca el primer `{` i intenta tancar-lo per emparellament
       de claus. Així extreu el primer objecte JSON ben format.
    4. Si tot falla, retorna {} (i el cridador aplica defaults).

    No llancem mai excepció: els fallbacks són millors que un crash en
    middle of conversation.
    """
    if text is None:
        return {}
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    if start == -1:
        return {}
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start: i + 1])
                except Exception:
                    return {}
    return {}


# ============================================================
# Prompts del sistema (català)
# ============================================================
# Tots els prompts del sistema són en català perquè volem que els
# missatges generats per la IA (raons, justificacions, pistes)
# arribin a l'alumne ja en català sense necessitat de cap traducció
# post-hoc. Els camps `step["text"]` i `enunciat` dels problemes es
# llegeixen amb `_loc(...)` que sempre retorna la versió catalana.

_SYSTEM_JUDGE = """
Ets un examinador rigorós però pacient i dialogant de problemes de probabilitat de batxillerat.
Reps UN pas atòmic d'una resolució i UNA resposta NOVA de l'alumne. Opcionalment reps també les
respostes anteriors del mateix alumne dins d'aquest mateix pas: un pas pot demanar diverses coses
alhora (per exemple: «defineix els successos i identifica les quatre probabilitats») i l'alumne
respon en torns, no necessàriament tot a la primera.
El pas pot ser raonament en text lliure, un càlcul numèric, o la identificació d'un espai mostral
/ succés. Accepta respostes informals en català/castellà/anglès.

Classifica la resposta NOVA tenint en compte la UNIÓ cumulativa amb les respostes anteriors,
en exactament una de:
  "correct"         — la unió cumulativa cobreix tot l'expected_summary i és matemàticament correcta.
  "incomplete"      — el que ha escrit (sol o en unió cumulativa amb les respostes anteriors) és
                      correcte i pertinent, però falten elements que el pas demana. La incompletud
                      NO és un buit conceptual: l'alumne va bé però no ha acabat.
  "typical_error"   — errònia, i coincideix amb un error conegut per a aquest pas.
  "conceptual_gap"  — errònia d'una manera que revela un concepte prerequisit que falta.

REGLA CLAU: si la resposta és un subconjunt correcte de l'esperada, marca "incomplete", NO
"typical_error" ni "conceptual_gap". Identificar bé dues de quatre probabilitats és "incomplete",
no un buit conceptual.

Respon ÚNICAMENT amb JSON vàlid, sense preàmbul ni markdown:
{"verdict": "...", "reason": "una frase en català (què està bé, o què falla)", "error_label": "etiqueta o null", "missing": "què falta en una frase (només si verdict=incomplete), o null", "next_question": "re-pregunta socràtica curta dirigida només al que falta (només si verdict=incomplete, màxim una frase, sense LaTeX, en català), o null"}
"""

_SYSTEM_DIAG = """
Estàs diagnosticant quin concepte prerequisit de probabilitat li falta a un alumne.
Reps un pas de resolució que ha fallat i la seva resposta errònia.
De la llista de dependències del problema, retorna la UNA que probablement falta.
Respon ÚNICAMENT amb JSON, sense preàmbul: {"dep_id": "...", "justification": "una frase"}
"""

_SYSTEM_HINT = """
Ets un tutor socràtic de probabilitat per a alumnes de batxillerat (17 anys).
L'alumne coneix el concepte però no l'aplica correctament.
Dona UNA pista mínima que l'orienti cap a la resposta sense revelar-la.
Màxim 2 frases. No facis servir LaTeX — usa matemàtiques ASCII o llenguatge planer.
Escriu la pista en català.
"""


# ============================================================
# Crida 1: jutjar pas de demostració
# ============================================================
def judge_step(step: dict, student_answer: str,
               step_partials: "list[str] | None" = None) -> dict:
    """
    Avalua una resposta de l'alumne a un pas concret d'un problema.

    Retorna un dict:
      {"verdict":       "correct" | "incomplete" | "typical_error" | "conceptual_gap",
       "reason":        str,
       "error_label":   str | None,
       "missing":       str | None,   # només quan verdict == "incomplete"
       "next_question": str | None}   # només quan verdict == "incomplete"

    `step_partials` són respostes ANTERIORS del mateix alumne dins d'aquest
    mateix pas, ja acceptades com a parcials en torns previs. El judge ha de
    considerar la UNIÓ cumulativa, no jutjar la nova resposta aïlladament:
    així una sèrie de respostes parcialment correctes pot tancar el pas amb
    `correct` quan la unió cobreix tot l'expected_summary. Quan és None o
    buida, el comportament és equivalent al d'abans (un sol torn).

    Si la IA retorna un veredicte fora del rang esperat, default a
    "typical_error" (millor un fals positiu d'error que un fals negatiu
    de "correct"; en cas d'injustícia, l'alumne tornarà a respondre amb
    més detall i la IA reavaluarà). NO defaulteggem a "incomplete" perquè
    no avança el pas i pot induir bucles si el model està desorientat.

    Aquesta és l'ÚNICA crida del codi viu sense plan B determinista
    per als passos de tipus `free_text`. Per als passos `integer`,
    `decimal`, `fraction` i `set_listing`, `tutor._check_*` ja decideix
    abans i només delega aquí com a fallback si l'input no parseja.
    """
    step_text = _loc(step['text'])
    partials_block = ""
    if step_partials:
        # Renderem les respostes prèvies amb un guió, perquè el model les
        # llegeixi com a entrades discretes i no les confongui amb la nova.
        joined = "\n".join(f"  - {p}" for p in step_partials)
        partials_block = (
            "\nPrevious answers from the same student in THIS step "
            "(already accepted as partial; use them for cumulative validation, "
            "do NOT re-judge them individually):\n"
            f"{joined}\n"
        )
    user_msg = f"""
Step presented to student:
  {step_text}

Expected answer summary (do NOT reveal to student):
  {step['expected_summary']}

Key concepts this step exercises:
  {step['key_concepts']}

Typical error for this step:
  {step['typical_error']} (label: {step['typical_error_label']})
{partials_block}
Student's new answer:
  {student_answer}

Classify the student's new answer considering the cumulative union with any previous answers.
"""
    # max_tokens lleugerament ampliat (200 → 250) perquè els veredictes
    # "incomplete" inclouen dos camps opcionals (missing, next_question).
    raw = _call_json(_SYSTEM_JUDGE, user_msg, max_tokens=250,
                     function_name="judge_step")
    data = _extract_json(raw)
    verdict = data.get("verdict", "typical_error")
    if verdict not in ("correct", "incomplete", "typical_error", "conceptual_gap"):
        verdict = "typical_error"
    return {
        "verdict":       verdict,
        "reason":        data.get("reason", ""),
        "error_label":   data.get("error_label"),
        "missing":       data.get("missing"),
        "next_question": data.get("next_question"),
    }


# ============================================================
# Crida 2: diagnosticar dependència
# ============================================================
def diagnose_dependency(step: dict, student_answer: str, problem: dict) -> str:
    """
    Quan `judge_step` ha marcat `conceptual_gap`, aquesta segona crida
    identifica QUINA dependència del problema està fallant.

    Passem només les dependències DECLARADES al problema (no totes les
    de `DEPENDENCIES`) per acotar les opcions del model i evitar
    classificacions arbitràries.

    Fallback determinista: si el `dep_id` retornat no existeix a
    `DEPENDENCIES`, retornem el primer de la llista del problema com a
    aproximació. Millor que None (que aturaria el retrocés).
    """
    deps_text = "\n".join(
        f"  {dep_id}: {_loc(DEPENDENCIES[dep_id]['description'])}"
        for dep_id in problem.get("dependencies", [])
        if dep_id in DEPENDENCIES
    )
    user_msg = f"""
Step: {_loc(step['text'])}
Student's wrong answer: {student_answer}
Problem dependencies:
{deps_text}
Which single dependency is most likely missing?
"""
    raw = _call_json(_SYSTEM_DIAG, user_msg, max_tokens=150,
                     function_name="diagnose_dependency")
    data = _extract_json(raw)
    dep_id = data.get("dep_id")
    if dep_id not in DEPENDENCIES:
        deps = problem.get("dependencies", [])
        return deps[0] if deps else None
    return dep_id


# ============================================================
# Crida 3: generar pista socràtica
# ============================================================
def generate_hint(step: dict, dep_id: str) -> str:
    """
    Pista socràtica curta per al pas actual, donat un concepte concret
    que l'alumne suposadament coneix però no està aplicant.

    Sortida en text lliure (no JSON). Es controla a través del prompt:
    màxim 2 frases, sense LaTeX, en català.

    Aquesta funció encara existeix tot i que no hi ha botó d'usuari per
    sol·licitar pista: és cridada internament des de
    `tutor._handle_conceptual_gap` quan el `_quick_keyword_check`
    detecta que l'alumne coneix el concepte (i per tant cal una pista
    en comptes d'un retrocés a prerequisit).
    """
    dep_desc = _loc(DEPENDENCIES.get(dep_id, {}).get("description", dep_id))
    user_msg = f"""
The student knows '{dep_desc}' but is not applying it.
Step they are on: {_loc(step['text'])}
Give one Socratic hint.
"""
    raw = _call_text(_SYSTEM_HINT, user_msg, max_tokens=120,
                     function_name="generate_hint")
    return raw.strip()
