"""
Logger d'interaccions amb l'API + tracking de cost.

Visió general per a un lector nou
=================================
Aquest mòdul és pur "side effect": cada crida a l'API Gemini que fa
`llm.py` arriba aquí i s'escriu al disc com a una línia JSON al fitxer
`logs/api_calls_YYYY-MM-DD.jsonl`. No té estat en memòria (excepte el
lock de threading).

Per què JSON Lines?
-------------------
- Append-only, thread-safe (només cal serialitzar l'`open` amb un lock).
- Fàcil d'analitzar amb eines estàndard (jq, pandas, awk).
- Una sessió pot generar 50+ entrades; un sol fitxer JSON gran seria
  pesat de reescriure cada cop.

Estructura d'una línia
----------------------
{
  "ts": "2026-05-07T18:36:00.123",   # timestamp ISO
  "session_id": "abc123",            # id de sessió (per problema iniciat)
  "student_id": "S001",              # pseudònim de l'alumne (None si no s'ha fixat)
  "function": "classify_error",      # nom de la funció d'alt nivell de llm.py
  "model": "gemini-2.5-pro",
  "attempt": 1,                      # nº d'intent (per retries)
  "ok": true,                        # ha tingut èxit?
  "elapsed_s": 4.2,                  # temps en segons
  "input": {...},                    # paràmetres de la crida (system truncat a 200 chars)
  "output": {...} | null,            # resposta (si OK)
  "tokens": {                        # null si la crida ha fallat
      "input": 1234,                 # prompt_token_count
      "output": 56,                  # candidates_token_count
      "thoughts": 234,               # thoughts_token_count (només thinking models)
      "total": 1524
  },
  "cost_usd": 0.00234,               # estimació de cost (null si KO)
  "error": "503 UNAVAILABLE" | null
}

Casos d'ús de `summarize_session(...)`
--------------------------------------
- Mostrar el cost a l'usuari durant la sessió en curs (mode debug d'app.py).
- Auditar el cost del pilot al final del dia (`session_id=None` → suma tot).
- Detectar funcions amb consum desproporcionat (`by_function`).
- Analítica per alumne (`student_id="S001"` → totes les sessions
  d'un alumne, agregades).

Manteniment de preus
--------------------
Els preus de `MODEL_PRICING_USD_PER_M` són d'última verificació 2026-05.
Cal revisar-los periòdicament a https://ai.google.dev/pricing.
Atenció: els preus poden tenir tarifa diferent per a context > 200k
tokens, però aquí assumim contextos curts (<5k) i no ho gestionem.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from threading import Lock

# Directori configurable via env. Per defecte `./logs/` relatiu al CWD,
# que és el que fa servir `app.py` quan s'aixeca amb `streamlit run`.
LOG_DIR = Path(os.environ.get("TUTOR_LOG_DIR", "logs"))

# Lock per a la concurrència de threads (Streamlit pot tenir múltiples
# usuaris simultanis escrivint al mateix fitxer). L'escriptura és
# append-only però l'`open` + `write` ha de ser atòmic per a evitar
# entrellaçar línies.
_lock = Lock()


# Preus en USD per 1 milió de tokens. Verificats el 2026-05.
# Hyperlinks de referència: https://ai.google.dev/pricing
#
# Particularitats:
# - Per a thinking models (gemini-2.5-pro), els thoughts_tokens es
#   facturen com a output_tokens. La nostra funció `estimate_cost_usd`
#   suma input + (output + thoughts), per tant el preu efectiu d'un
#   thinking pot ser molt més alt que el d'un flash.
# - Si Google introdueix un model nou no llistat aquí, s'aplica
#   `_FALLBACK_PRICING` (preu conservador del pro).
MODEL_PRICING_USD_PER_M = {
    "gemini-2.5-pro":        {"input": 1.25, "output": 10.00},
    "gemini-2.5-flash":      {"input": 0.30, "output":  2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "output":  0.40},
}
_FALLBACK_PRICING = {"input": 1.25, "output": 10.00}


def estimate_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    """
    Cost d'una crida en USD a partir dels comptes de tokens.

    Per al thinking model `gemini-2.5-pro`, el cridador ha de passar la
    suma `candidates_tokens + thoughts_tokens` com a `tokens_out`. La
    funció `log_call` ho fa automàticament; només cal preocupar-se'n si
    es crida `estimate_cost_usd` directament.
    """
    rates = MODEL_PRICING_USD_PER_M.get(model, _FALLBACK_PRICING)
    return (tokens_in * rates["input"] + tokens_out * rates["output"]) / 1_000_000


def _ensure_dir():
    """Crea `logs/` al primer ús. Idempotent."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _logfile_path() -> Path:
    """Path del fitxer del dia actual: `logs/api_calls_YYYY-MM-DD.jsonl`.
    Es regenera a cada crida (no cachegem) perquè una sessió que travessa
    la mitjanit ha d'escriure als dos fitxers correctament."""
    today = datetime.now().strftime("%Y-%m-%d")
    return LOG_DIR / f"api_calls_{today}.jsonl"


def log_call(session_id: str, function: str, model: str,
             attempt: int, ok: bool, elapsed_s: float,
             input_data: dict, output_data=None, error: str = None,
             tokens: dict = None, student_id: str = None):
    """
    Escriu una entrada al log. Thread-safe.

    Paràmetres:
    - `session_id`, `student_id`: identifiquen la sessió i l'alumne. Es
      proveeixen pel cridador (`llm._call_with_retry` els llegeix del
      `set_log_context`).
    - `function`: nom de la funció d'alt nivell (`judge_step`,
      `diagnose_dependency`, `generate_hint`). Permet agregar per
      funció a `summarize_session`.
    - `attempt`: nº d'intent (1, 2, 3...) per als retries.
    - `ok`: si la crida ha tingut èxit.
    - `tokens`: dict opcional amb camps {input, output, thoughts, total}.
      Si està present i `ok=True`, també calcula el cost USD.
    - `error`: cadena de l'excepció si `ok=False`.
    """
    _ensure_dir()
    cost_usd = None
    if tokens is not None and ok:
        # Els thinking tokens es facturen com a output (regla actual de
        # Gemini 2.5 Pro). Per tant els sumem abans del càlcul.
        out_total = (tokens.get("output", 0) or 0) + (tokens.get("thoughts", 0) or 0)
        in_total = tokens.get("input", 0) or 0
        cost_usd = round(estimate_cost_usd(model, in_total, out_total), 6)
    entry = {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "session_id": session_id,
        "student_id": student_id,
        "function": function,
        "model": model,
        "attempt": attempt,
        "ok": ok,
        "elapsed_s": round(elapsed_s, 3),
        "input": input_data,
        "output": output_data,
        "tokens": tokens,
        "cost_usd": cost_usd,
        "error": error,
    }
    # `ensure_ascii=False` perquè els missatges de prompt poden contenir
    # accents i caràcters matemàtics (∈, ◁, ℤ...). Volem que el log sigui
    # llegible directament amb less o tail.
    line = json.dumps(entry, ensure_ascii=False)
    with _lock:
        with open(_logfile_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")


def get_log_path() -> Path:
    """Path del log actiu, per si la UI vol mostrar-lo o oferir descàrrega."""
    return _logfile_path()


def summarize_session(session_id: str = None, log_path: Path = None,
                      student_id: str = None) -> dict:
    """
    Agrega estadístiques de crides al log. Sense filtres: tots els
    fitxers del directori, totes les sessions. Filtres acumulables:
      - `session_id`: només crides d'aquesta sessió
      - `student_id`: només crides d'aquest alumne (totes les sessions)
      - `log_path`: només aquest fitxer (default: tots els .jsonl)

    Retorna:
      {
        "session_id": str | None,
        "student_id": str | None,
        "calls_total": int,
        "calls_ok": int,
        "calls_failed": int,
        "by_function": {nom: comptador},
        "tokens_input": int,
        "tokens_output": int,   # inclou thoughts (com a la facturació)
        "tokens_total": int,
        "cost_usd": float,      # estimació
        "elapsed_s_total": float,
      }

    Implementació: read-through linear de tots els fitxers candidats.
    Per al volum del pilot (uns pocs MB/dia), és prou ràpid; si arribem
    a desenes de MB, val la pena indexar.
    """
    files = [log_path] if log_path else sorted(LOG_DIR.glob("api_calls_*.jsonl"))
    summary = {
        "session_id": session_id,
        "student_id": student_id,
        "calls_total": 0,
        "calls_ok": 0,
        "calls_failed": 0,
        "by_function": {},
        "tokens_input": 0,
        "tokens_output": 0,
        "tokens_total": 0,
        "cost_usd": 0.0,
        "elapsed_s_total": 0.0,
    }
    for f in files:
        if not f or not f.exists():
            continue
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except Exception:
                    # Línia corrupta (escriptura interrompuda?). La saltem
                    # silenciosament: és més útil tenir un resum aproximat
                    # que un crash.
                    continue
                # Aplica els filtres acumulativament: tots els passos
                # s'han de complir.
                if session_id is not None and entry.get("session_id") != session_id:
                    continue
                if student_id is not None and entry.get("student_id") != student_id:
                    continue
                summary["calls_total"] += 1
                if entry.get("ok"):
                    summary["calls_ok"] += 1
                else:
                    summary["calls_failed"] += 1
                fn = entry.get("function", "unknown")
                summary["by_function"][fn] = summary["by_function"].get(fn, 0) + 1
                summary["elapsed_s_total"] += entry.get("elapsed_s", 0) or 0
                tk = entry.get("tokens") or {}
                summary["tokens_input"] += tk.get("input", 0) or 0
                summary["tokens_output"] += ((tk.get("output", 0) or 0)
                                             + (tk.get("thoughts", 0) or 0))
                summary["tokens_total"] += tk.get("total", 0) or 0
                if entry.get("cost_usd") is not None:
                    summary["cost_usd"] += entry["cost_usd"]
    summary["cost_usd"] = round(summary["cost_usd"], 6)
    summary["elapsed_s_total"] = round(summary["elapsed_s_total"], 2)
    return summary
