# Tutor de Probabilitat

Tutor Socràtic pas a pas per a problemes de probabilitat de batxillerat
(~17 anys). Interfície monolingüe en català; sessions anònimes.

Cobreix: regla de Laplace, probabilitat condicionada, probabilitat
total, teorema de Bayes, distribució binomial.

## Origen

Aquest projecte reutilitza l'arquitectura de dos prototips germans:

- **`tutor-grups`** — base principal. La màquina d'estats (`tutor.py`),
  les crides a la IA (`llm.py`), el logger (`api_logger.py`) i la UI
  (`app.py`) es copien gairebé verbatim. Vegeu `tutor.py:1-30` per al
  llistat exhaustiu de components reutilitzats.
- **`tutor-eq`** — d'aquí venen dues addicions: la detecció determinista
  d'ús inadequat (`_handle_inappropriate`) i el patró de verificació
  numèrica (`_check_numeric` amb `fractions.Fraction`).

Tota la lògica del domini viu a `problems.py` (catàleg d'errors, graf de
dependències, prerequisits, problemes).

## Execució

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...
streamlit run app.py
```

Mode debug (mostra l'estat intern i el rastre JSON):

```
http://localhost:8501/?debug=1
```

Tests (no requereixen la clau API):

```bash
python -m unittest discover tests
```

## Fitxers

| Fitxer | Descripció |
|---|---|
| `tutor.py`         | Màquina d'estats. `process_turn` és el punt d'entrada únic. |
| `llm.py`           | 3 crides a Gemini: `judge_step`, `diagnose_dependency`, `generate_hint`. |
| `api_logger.py`    | Logger JSONL append-only amb tracking de cost. |
| `app.py`           | UI Streamlit (no té lògica de domini). |
| `problems.py`      | `PROBLEMS`, `DEPENDENCIES`, `PREREQUISITES`, `ERROR_CATALOG`, `PILOT_PATH`. |
| `data/pilot_problems.py` | Slice del camí pilot sobre `PROBLEMS`. |
| `DAG.md`           | Graf de continguts: 11 nodes, 12 arestes. |
| `SCHEMA.md`        | Esquema complet de les estructures de dades i de l'estat de sessió. |
| `tests/`           | Tests unitaris (estat, IA mockejada, verificadors numèrics). |

## Camí pilot

```
PROB-LAP-01 → PROB-LAP-02 → PROB-TOT-01 → PROB-BAY-01 → PROB-BIN-01 → PROB-BIN-02
```

Cada problema té 2-3 passos. Els tipus d'input cobrint el pilot són
`free_text`, `integer`, `decimal` i `fraction`. Veure `SCHEMA.md` per al
contracte complet.
