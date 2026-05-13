# DAG — Graf de continguts: Probabilitat (batxillerat)

11 nodes, 12 arestes. Acíclic, feblment connex, mínim.

## Nodes

| ID | Capa | Concepte |
|---|---|---|
| PROB-L0-ESPAI | 0 | Espai mostral; successos; succés complementari |
| PROB-L0-FREQ  | 0 | Freqüència relativa; probabilitat empírica |
| PROB-L1-LAP   | 1 | Regla de Laplace: P(A) = \|favorables\| / \|totals\| |
| PROB-L1-AXI   | 1 | Axiomes de Kolmogorov; probabilitat de la unió |
| PROB-L2-COM   | 2 | Probabilitat condicionada P(A\|B) = P(A∩B) / P(B) |
| PROB-L2-IND   | 2 | Independència: P(A∩B) = P(A)·P(B) |
| PROB-L2-TOT   | 2 | Teorema de la probabilitat total |
| PROB-L3-BAY   | 3 | Teorema de Bayes |
| PROB-L3-ARB   | 3 | Arbres de probabilitat (eina transversal) |
| PROB-L4-VD    | 4 | Variable aleatòria discreta; distribució de probabilitat |
| PROB-L4-BIN   | 4 | Distribució binomial B(n, p) |

## Arestes (prerequisit → successor)

```
PROB-L0-ESPAI → PROB-L1-LAP
PROB-L0-ESPAI → PROB-L1-AXI
PROB-L0-ESPAI → PROB-L3-ARB
PROB-L1-LAP   → PROB-L1-AXI
PROB-L1-AXI   → PROB-L2-COM
PROB-L2-COM   → PROB-L2-IND
PROB-L2-COM   → PROB-L2-TOT
PROB-L2-TOT   → PROB-L3-BAY
PROB-L2-IND   → PROB-L4-BIN
PROB-L3-ARB   → PROB-L3-BAY
PROB-L4-VD    → PROB-L4-BIN
```

Nota: `PROB-L0-FREQ` no apareix en cap aresta del pilot — és un node de
context (probabilitat empírica) que no és prerequisit dur de cap dels
problemes pilot. Es manté al graf perquè és part del currículum estàndard
de batxillerat i serà rellevant si s'amplien els problemes amb estimació
empírica o llei dels grans nombres.

## Camí pilot

```
PROB-L0-ESPAI → PROB-L1-LAP → PROB-L2-COM → PROB-L2-TOT
              → PROB-L3-BAY → PROB-L4-BIN ★
```

## Cobertura del pilot

| Problema | Node | Prerequisits exercitats |
|---|---|---|
| PROB-LAP-01 | PROB-L1-LAP | `def_espai_mostral`, `def_laplace` |
| PROB-LAP-02 | PROB-L1-LAP | `def_combinatoria`, `def_laplace` |
| PROB-TOT-01 | PROB-L2-TOT | `def_prob_condicionada`, `def_prob_total` |
| PROB-BAY-01 | PROB-L3-BAY | `def_prob_condicionada`, `def_prob_total`, `def_bayes` |
| PROB-BIN-01 | PROB-L4-BIN | `def_combinatoria`, `def_binomial` |
| PROB-BIN-02 | PROB-L4-BIN | `def_binomial`, `def_independencia` |

L'arbre (`PROB-L3-ARB`) s'introdueix com a eina conceptual per a la
representació de problemes amb branques (probabilitat total i Bayes),
no com una família de problemes pròpia. En el pilot apareix
implícitament a `PROB-TOT-01` i `PROB-BAY-01`; una futura ampliació
podria afegir problemes etiquetats amb `"tool": "tree"`.
