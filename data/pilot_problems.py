"""
Camí pilot del Tutor de Probabilitat: 6 problemes que cobreixen Laplace,
probabilitat total, Bayes i binomial.

Visió general per a un lector nou
=================================
Aquest fitxer NO conté dades pròpies: és una vista (slice) sobre `PROBLEMS`
i `PILOT_PATH` definits a `problems.py`. Existeix perquè durant la fase
pilot només volem treballar amb el subconjunt de problemes del camí
estàndard, no amb tots els que algun dia pugui tenir la base de dades.

Per què està a `data/` i no a l'arrel?
--------------------------------------
Convenció: `problems.py` és el codi viu (esquemes, getters, validació
mínima). `data/` queda reservat per a subsets, fixtures i futures
exportacions (p.ex. catàlegs de problemes generats per a un curs concret).

Ús típic:
    from data.pilot_problems import PILOT_PROBLEMS, PILOT_PATH

Execució directa (per a verificar el contingut del camí pilot):
    python data/pilot_problems.py

Sortida: línia per problema amb id, node del DAG i nº de passos atòmics.
"""

import sys
import os

# Truc per fer importable `problems` quan executem aquest fitxer
# directament des de `data/` (sense aquest path-hack, `from problems
# import PROBLEMS` falla perquè `data/` no és al sys.path).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from problems import PROBLEMS, PILOT_PATH

# Diccionari id → problema, restringit al camí pilot. La comprensió amb
# `if pid in PROBLEMS` és defensiva: si algú edita PILOT_PATH afegint un
# id que no existeix, no peta — simplement queda fora del subset.
PILOT_PROBLEMS = {pid: PROBLEMS[pid] for pid in PILOT_PATH if pid in PROBLEMS}

if __name__ == "__main__":
    # Inspecció ràpida del contingut: útil per a confirmar que el camí
    # pilot està ben definit després d'un canvi a `problems.py`.
    print(f"Camí pilot: {len(PILOT_PATH)} problemes")
    for pid in PILOT_PATH:
        p = PROBLEMS.get(pid)
        if p:
            n_steps = len(p["passos"])
            print(f"  {pid:20s}  node={p['node']:15s}  passos={n_steps}")
