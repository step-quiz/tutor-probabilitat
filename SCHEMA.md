# SCHEMA.md — Estructures de dades: Tutor de Probabilitat

> **Nota sobre el bilingüisme.** Els camps marcats com a `{"ca": str, "en": str}`
> mantenen tots dos idiomes a les dades, però la UI i l'engine només
> renderitzen el català. `problems.get_localized(field)` retorna sempre
> l'entrada "ca" (amb fallback a "en" si "ca" no existeix). Les entrades
> "en" són reserva per a una futura extensió multilingüe; ara mateix no
> les llegeix ningú.

## PROBLEMS

Cada entrada de `PROBLEMS` té:

```python
{
  "id":             str,    # e.g. "PROB-BAY-01"
  "node":           str,    # DAG node: "PROB-L3-BAY"
  "familia":        str,    # "PROB-BAY"
  "nivell":         int,    # 1=immediat, 2=intermedi, 3=demostració/derivació, 4=avançat
  "tema":           {"ca": str, "en": str},   # títol breu bilingüe
  "enunciat":       {"ca": str, "en": str},   # enunciat complet per a l'alumne
  "input_mode":     str,    # "free_text" | "structured"
  "answer_language": str,   # "ca" | "en" | "es"
  "dependencies":   list,   # [dep_id, ...] → claus de DEPENDENCIES
  "errors_freqüents": list, # [error_label, ...] → claus d'ERROR_CATALOG
  "passos": [
    {
      "id":                  int,
      "text":                {"ca": str, "en": str},  # pregunta per a l'alumne
      "expected_summary":    str,    # resum de resposta correcta (NO visible per a l'alumne)
      "key_concepts":        list,   # [dep_id, ...] exercitats en aquest pas
      "input_type":          str,    # vegeu enumeració més avall
      "expected_value":      any,    # tipus segons input_type, o None per a free_text
      "typical_error":       str,    # descripció en anglès de l'error típic
      "typical_error_label": str,    # clau d'ERROR_CATALOG
    }
  ]
}
```

### Enumeració de `input_type`

| Valor | `expected_value` | Verificació | Fallback |
|---|---|---|---|
| `"free_text"`   | `None`                  | LLM (`judge_step`)                 | — |
| `"integer"`     | `int` (ex: `4`)         | comparació exacta amb tolerància   | LLM |
| `"set_listing"` | `list[str]` (ex: `["HH","HT","TH"]`) | comparació de conjunts (case-insensitive, ignora claus/parèntesis) | LLM |
| `"decimal"`     | `float` (ex: `0.038`)   | comparació numèrica amb tolerància `1e-4` via `Fraction` | LLM |
| `"fraction"`    | `str | float` (ex: `"9/19"`, `0.4737`) | comparació numèrica amb tolerància `1e-4` via `Fraction` | LLM |

La verificació numèrica accepta tant punt com coma decimal (`0.5` ≡ `0,5`)
i tolera espais. Si el text de l'alumne NO parseja com un número / conjunt
(p.ex., raonament en prosa), el verificador retorna `None` i l'engine
delega a `llm.judge_step` per al judici final. Aquest fallback és el que
fa que l'alumne pugui escriure raonament textual encara que el pas
demanés un número.

## DEPENDENCIES

```python
{
  dep_id: {
    "description":  {"ca": str, "en": str},   # descripció per als prompts i pistes
    "keywords":     list[str],                # paraules clau (substring, case-insensitive)
    "prerequisite": str,                      # PRE-xxx (clau de PREREQUISITES)
    "dag_node":     str,                      # PROB-Lx-yyy (clau del DAG)
  }
}
```

## PREREQUISITES

```python
{
  prereq_id: {
    "id":                str,
    "concept":           str,                      # dep_id associat
    "question":          {"ca": str, "en": str},   # pregunta del mini-exercici
    "keywords_required": list[str],                # l'alumne ha d'incloure'n alguna
    "forbidden_keywords": list[str],               # si en troba alguna, és incorrecta
    "explanation":       {"ca": str, "en": str},   # explicació mostrada en tots dos casos
  }
}
```

L'avaluació de prereqs és deterministica (substring match, case-insensitive,
sense word-boundary). NO crida la IA. Veure `tutor._process_prereq_turn`.

## ERROR_CATALOG

```python
{
  error_label: {"ca": str, "en": str},   # missatge mostrat a l'alumne quan la IA assigna aquesta etiqueta
}
```

## Estat de sessió (tutor.py)

```python
{
  "session_id":             str,
  "student_id":             None,            # sempre None: sessió anònima
  "problem_id":             str,
  "problem":                dict,            # còpia del problema (per evitar mutar PROBLEMS)
  "started_at":             str,             # ISO 8601
  "started_at_ts":          float,           # epoch seconds
  "current_step_idx":       int,             # índex a problem["passos"]
  "history":                list,            # torns registrats (vegeu §History)
  "stagnation_consecutive": int,
  "backtrack_count":        int,             # nº total de retrocessos a prereq
  "backtrack_depth":        int,             # profunditat actual del retrocés
  "active_prereq":          str | None,      # PRE-xxx si dins d'un mini-exercici
  "active_prereq_depth":    int,
  "concept_failure_streak": dict,            # {dep_id: count}
  "verdict_final":          str | None,      # "solved" | "referred_to_tutor" | "suspended"
  "nodes_consolidated":     list,            # DAG nodes consolidats en sessió
  "pending_message":        None,            # slot reservat (no usat)
  "inappropriate_warnings": int,             # comptador d'ús inadequat (vegeu §Ús inadequat)
  "messages":               list,            # {kind, text, persistent, ts}
}
```

### Veredictes finals

| Valor | Significat |
|---|---|
| `None`                 | sessió en curs |
| `"solved"`             | tots els passos completats correctament |
| `"referred_to_tutor"`  | s'ha arribat a `MAX_BACKTRACK_DEPTH = 2` sense desbloquejar |
| `"suspended"`          | s'ha arribat a `MAX_INAPPROPRIATE_WARNINGS = 3` (ús inadequat) |

Nota: no existeix cap senyal d'usuari per abandonar la sessió. Si
l'alumne tanca la pestanya, la sessió queda perduda (l'estat viu només
a `st.session_state`).

### Ús inadequat

`tutor._has_math_content(raw_input)` és una heurística determinista
basada en presència de dígits, operadors matemàtics o vocabulari del
domini. Si retorna `False`, l'engine crida `_handle_inappropriate`, que
incrementa `inappropriate_warnings` i, al 3r avís, marca `verdict_final
= "suspended"`. Si en un torn posterior torna a haver-hi contingut
matemàtic, el comptador es reseteja a 0.

Aquest mecanisme és portat de `tutor-eq` (vegeu §11 del seu disseny).
