"""
Base de dades del Tutor de Probabilitat (batxillerat, ~17 anys).

Visió general per a un lector nou
=================================
Aquest fitxer és la "constant de referència" del sistema: tot el que es
mostra a l'alumne (enunciats, passos atòmics, prerequisits) i tot el que
la IA consulta com a context (errors típics, dependències) viu aquí com
a dades Python (no com a base de dades externa).

Quatre estructures principals + un mapatge auxiliar
---------------------------------------------------
1. `ERROR_CATALOG` (dict id → {ca, en})
   Catàleg d'etiquetes d'errors que la IA pot assignar quan classifica
   una resposta errònia. Cada problema declara quins errors d'aquest
   catàleg són "esperables" via el camp `typical_error_label` de cada
   pas. L'esquema està documentat a `SCHEMA.md`.

2. `DEPENDENCIES` (dict concept_id → {description, keywords, prerequisite, dag_node})
   Conceptes prerequisit del graf. Cada concepte té un `prerequisite`
   que apunta a una entrada de `PREREQUISITES`, i un `dag_node` que el
   lliga al graf documentat a `DAG.md`. Els `keywords` s'usen per a la
   verificació ràpida (`tutor._quick_keyword_check`) que decideix si
   l'alumne mostra coneixement del concepte malgrat fallar la
   resposta.

3. `PREREQUISITES` (dict prereq_id → {concept, question, keywords_required, ..., explanation})
   Mini-problemes que activem com a "exercici de reforç" quan detectem
   un buit conceptual. L'avaluació és DETERMINISTA (keyword matching),
   no IA. Veure `tutor._process_prereq_turn`.

4. `PROBLEMS` (dict problem_id → {id, node, familia, nivell, tema, enunciat, passos, dependencies, ...})
   Els problemes pròpiament dits. Cada problema és una RESOLUCIÓ que
   l'alumne ha de fer pas a pas. La clau és el camp `passos`: una llista
   de passos atòmics, cadascun amb la pregunta socràtica, el resum
   esperat (no es mostra a l'alumne), els conceptes que exercita, i
   l'error típic associat.

5. `_ERROR_TO_DEPENDENCY` (dict private)
   Mapatge fallback: si la IA marca un error però no identifica la
   dependència, podem inferir-la deterministicament des de l'etiqueta.

Convenció bilingüe
------------------
Tots els camps visibles per a l'alumne (`tema`, `enunciat`, text dels
`passos`, `question` dels prereqs, `description` de dependències,
missatges del catàleg d'errors) tenen format `{"ca": "...", "en": "..."}`.
`get_localized(field, lang)` aplana segons la llengua activa. Els camps
NO visibles per a l'alumne (ids, keywords, `expected_summary`, `node`...)
són strings simples.

`PILOT_PATH` defineix el subconjunt de problemes actiu durant la fase
pilot, ordenats pel camí lògic d'avançament: Laplace → comptatge →
probabilitat total → Bayes → binomial.

Tipus d'input acceptats
-----------------------
Els passos admeten cinc valors de `input_type`:
  - "free_text"   → raonament en text lliure. Avaluat per la IA.
  - "integer"     → enter (ex: nombre de casos favorables). Verificat
                    deterministicament; fallback a IA si no parseja.
  - "decimal"     → decimal amb tolerància 1e-5 (ex: 0.038).
  - "fraction"    → fracció o decimal (ex: "9/19", "0.4737").
  - "set_listing" → llista d'elements (ex: "{HH, HT, TH}").
Veure `tutor._check_numeric`, `_check_integer`, `_check_set`.
"""

# ============================================================
# CATÀLEG D'ERRORS TÍPICS
# ============================================================
# Cada entrada: id → {ca: "...", en: "..."} amb el missatge que es
# mostrarà a l'alumne quan la IA assigni aquesta etiqueta. El prefix
# de l'id indica la família:
#   GEN_*  → errors genèrics, no específics d'un concepte
#   LAP_*  → errors al voltant de la regla de Laplace
#   COND_* → errors al voltant de probabilitat condicionada
#   TOT_*  → errors al voltant del teorema de la probabilitat total
#   BAY_*  → errors al voltant del teorema de Bayes
#   BIN_*  → errors al voltant de la distribució binomial
#
# Convenció: cada etiqueta ha de ser referenciable des d'un pas
# (`typical_error_label` a PROBLEMS). Si afegeixes una etiqueta nova,
# considera afegir-la també a `_ERROR_TO_DEPENDENCY` per activar el
# fallback determinista de retrocés a prereq.
ERROR_CATALOG = {
    # ---- Laplace ----
    "LAP_espai_no_equiprobable": {
        "ca": "Aplica la regla de Laplace sobre un espai mostral no equiprobable",
        "en": "Applies Laplace's rule on a non-equiprobable sample space",
    },
    "LAP_favorable_total_swap": {
        "ca": "Inverteix favorables i totals al càlcul (posa els totals al numerador)",
        "en": "Swaps favourable and total cases (puts total in the numerator)",
    },
    "LAP_doble_recompte": {
        "ca": "Compta dues vegades el mateix cas en l'enumeració",
        "en": "Counts the same case twice when enumerating",
    },
    # ---- Condicionada / independència ----
    "COND_invertit": {
        "ca": "Confon P(A|B) amb P(B|A) (probabilitat condicionada invertida)",
        "en": "Confuses P(A|B) with P(B|A) (inverted conditional)",
    },
    "COND_conjunta_vs_condicio": {
        "ca": "Usa P(A∩B) on calia P(A|B), o viceversa",
        "en": "Uses P(A∩B) where P(A|B) is needed, or vice versa",
    },
    "IND_assumida": {
        "ca": "Assumeix independència sense justificar-la",
        "en": "Assumes independence without justification",
    },
    # ---- Probabilitat total ----
    "TOT_branca_oblidada": {
        "ca": "Omet una branca a la fórmula de la probabilitat total",
        "en": "Omits one branch in the total probability formula",
    },
    "TOT_suma_vs_producte": {
        "ca": "Suma al llarg d'una branca quan caldria multiplicar",
        "en": "Adds along a branch instead of multiplying",
    },
    # ---- Bayes ----
    "BAY_invertit": {
        "ca": "Inverteix numerador i denominador a la fórmula de Bayes",
        "en": "Inverts numerator and denominator in Bayes' formula",
    },
    "BAY_no_prob_total": {
        "ca": "No calcula P(B) per la probabilitat total al denominador de Bayes",
        "en": "Does not compute P(B) via total probability for the Bayes denominator",
    },
    "BAY_confon_conjunta": {
        "ca": "Usa P(A∩B) al denominador de Bayes en comptes de P(B)",
        "en": "Uses P(A∩B) as the Bayes denominator instead of P(B)",
    },
    # ---- Binomial ----
    "BIN_n_k_invertits": {
        "ca": "Inverteix n i k al coeficient binomial C(n,k)",
        "en": "Swaps n and k in the binomial coefficient C(n,k)",
    },
    "BIN_complementari_oblidat": {
        "ca": "Calcula P(X=k) quan calia P(X≥k) (oblida el complementari o la suma)",
        "en": "Computes P(X=k) when P(X≥k) is asked (forgets complement or sum)",
    },
    "BIN_p_vs_q": {
        "ca": "Usa p on calia q=1−p (o viceversa) a la fórmula binomial",
        "en": "Uses p where q=1−p is needed (or vice versa) in the binomial formula",
    },
    # ---- Genèrics ----
    "GEN_arithmetic": {
        "ca": "Error aritmètic de càlcul",
        "en": "Arithmetic computation error",
    },
    "GEN_other": {
        "ca": "Altre error no catalogat",
        "en": "Other error not listed in catalog",
    },
}


# ============================================================
# MAPATGE FALLBACK: etiqueta d'error → concepte prerequisit
# ============================================================
# Quan `judge_step` retorna `typical_error` amb etiqueta, però la
# diagnosi posterior (`diagnose_dependency`) no s'arriba a cridar (perquè
# `typical_error` no dispara retrocés), podem voler-ne disparar-lo
# igualment si l'etiqueta implica clarament un concepte. Aquest mapatge
# és el fallback determinista. Actualment NO el consulta el codi viu
# (només `conceptual_gap` dispara retrocés), però queda per a futures
# heurístiques i per a anàlisi off-line del rastre.
#
# Convenció: només omplir entrades on l'etiqueta implica
# INEQUÍVOCAMENT un concepte. Si una etiqueta és ambigua (`GEN_other`),
# no s'inclou — millor no disparar retrocés que enviar l'alumne a un
# prereq aleatori.
_ERROR_TO_DEPENDENCY = {
    # Laplace
    "LAP_espai_no_equiprobable": "def_laplace",
    "LAP_favorable_total_swap":  "def_laplace",
    "LAP_doble_recompte":        "def_combinatoria",
    # Condicionada
    "COND_invertit":             "def_prob_condicionada",
    "COND_conjunta_vs_condicio": "def_prob_condicionada",
    "IND_assumida":              "def_independencia",
    # Probabilitat total
    "TOT_branca_oblidada":       "def_prob_total",
    "TOT_suma_vs_producte":      "def_prob_total",
    # Bayes
    "BAY_invertit":              "def_bayes",
    "BAY_no_prob_total":         "def_prob_total",
    "BAY_confon_conjunta":       "def_bayes",
    # Binomial
    "BIN_n_k_invertits":         "def_binomial",
    "BIN_complementari_oblidat": "def_binomial",
    "BIN_p_vs_q":                "def_binomial",
}


def implied_dependency_for_error(error_label):
    """
    Retorna la dependència implicada per una etiqueta d'error, o None.
    Veure comentari de `_ERROR_TO_DEPENDENCY` sobre la política.
    """
    return _ERROR_TO_DEPENDENCY.get(error_label)


def get_localized(field) -> str:
    """
    Aplana un camp a un string en català.

    Accepta:
    - `dict` de la forma `{"ca": "...", "en": "..."}` → retorna l'entrada
      "ca". Si no hi és, fallback a "en", i a la primera entrada present.
      (Les dades de `problems.py` mantenen un esquema bilingüe per
      simplicitat — `get_localized` ignora qualsevol cosa que no sigui
      català, però el camp es queda allà sense fer mal.)
    - `str` simple → es retorna sense modificar (camps legacy o no
      bilingües, com ara els ids o els valors de keywords).

    Garanteix que mai retornarà None: si tot falla, retorna string buit.
    """
    if isinstance(field, dict):
        return field.get("ca") or field.get("en") or next(iter(field.values()), "")
    return field  # plain string (legacy) — return as-is


# ============================================================
# DEPENDÈNCIES (conceptes del graf DAG)
# ============================================================
# Cada entrada: concept_id → {
#   description: {ca, en}     # text mostrable a l'alumne en pistes/prereqs
#   keywords: [str, ...]      # paraules clau per al keyword-match determinista
#   prerequisite: prereq_id   # mini-problema activable per a retrocés
#   dag_node: str             # node del graf de continguts (veure DAG.md)
# }
#
# Aquesta estructura és l'enllaç entre tres mons:
#   - El graf DAG documentat a DAG.md (via `dag_node`).
#   - El catàleg de mini-exercicis a PREREQUISITES (via `prerequisite`).
#   - Els problemes principals (via `dependencies` als problemes
#     individuals i `key_concepts` a cada pas).
DEPENDENCIES = {
    "def_espai_mostral": {
        "description": {
            "ca": "espai mostral: conjunt de tots els resultats possibles d'un experiment",
            "en": "sample space: set of all possible outcomes of an experiment",
        },
        "keywords": ["espai mostral", "sample space", "resultats", "outcomes",
                     "equiprobable", "casos possibles"],
        "prerequisite": "PRE-ESPAI",
        "dag_node": "PROB-L0-ESPAI",
    },
    "def_combinatoria": {
        "description": {
            "ca": "combinatòria bàsica: comptar amb ordre o sense, repetició o no",
            "en": "basic combinatorics: counting with/without order, with/without repetition",
        },
        "keywords": ["combinatori", "combinator", "permutaci", "permutation",
                     "variaci", "variation", "factorial", "C(", "nCk"],
        "prerequisite": "PRE-COMB",
        "dag_node": "PROB-L0-ESPAI",
    },
    "def_laplace": {
        "description": {
            "ca": "regla de Laplace: P(A) = casos favorables / casos possibles, en espai equiprobable",
            "en": "Laplace's rule: P(A) = favourable / total cases, on an equiprobable space",
        },
        "keywords": ["laplace", "favorable", "favorables", "total", "casos",
                     "equiprobable"],
        "prerequisite": "PRE-LAP",
        "dag_node": "PROB-L1-LAP",
    },
    "def_prob_condicionada": {
        "description": {
            "ca": "probabilitat condicionada: P(A|B) = P(A∩B) / P(B), amb P(B)>0",
            "en": "conditional probability: P(A|B) = P(A∩B) / P(B), with P(B)>0",
        },
        "keywords": ["condicionada", "conditional", "donat", "given",
                     "P(A|B)", "P(A\\|B)", "condicional"],
        "prerequisite": "PRE-COND",
        "dag_node": "PROB-L2-COM",
    },
    "def_independencia": {
        "description": {
            "ca": "independència: A i B són independents si P(A∩B) = P(A)·P(B)",
            "en": "independence: A and B are independent if P(A∩B) = P(A)·P(B)",
        },
        "keywords": ["independent", "independents", "independencia",
                     "independence", "producte", "product"],
        "prerequisite": "PRE-IND",
        "dag_node": "PROB-L2-IND",
    },
    "def_prob_total": {
        "description": {
            "ca": "teorema de la probabilitat total: P(B) = Σᵢ P(B|Aᵢ)·P(Aᵢ) per a una partició {Aᵢ}",
            "en": "total probability theorem: P(B) = Σᵢ P(B|Aᵢ)·P(Aᵢ) for a partition {Aᵢ}",
        },
        "keywords": ["probabilitat total", "total probability", "partició",
                     "partition", "branca", "branch", "rama", "arbre", "tree"],
        "prerequisite": "PRE-TOT",
        "dag_node": "PROB-L2-TOT",
    },
    "def_bayes": {
        "description": {
            "ca": "teorema de Bayes: P(A|B) = P(B|A)·P(A) / P(B)",
            "en": "Bayes' theorem: P(A|B) = P(B|A)·P(A) / P(B)",
        },
        "keywords": ["bayes", "posterior", "prior", "verosimilitud", "likelihood"],
        "prerequisite": "PRE-BAY",
        "dag_node": "PROB-L3-BAY",
    },
    "def_binomial": {
        "description": {
            "ca": "distribució binomial B(n,p): P(X=k) = C(n,k)·pᵏ·(1−p)ⁿ⁻ᵏ",
            "en": "binomial distribution B(n,p): P(X=k) = C(n,k)·p^k·(1−p)^(n−k)",
        },
        "keywords": ["binomial", "B(", "n assajos", "trials", "èxit", "success",
                     "fracàs", "failure", "C(n", "coeficient binomial"],
        "prerequisite": "PRE-BIN",
        "dag_node": "PROB-L4-BIN",
    },
}


# ============================================================
# PREREQUISITS (mini-problemes per al retrocés)
# ============================================================
# Cada entrada: prereq_id → {
#   id, concept, question: {ca, en},
#   keywords_required: [str, ...]    # almenys una ha d'aparèixer a la resposta
#   forbidden_keywords: [str, ...]   # cap d'aquestes pot aparèixer
#   explanation: {ca, en}            # text mostrat al tancar el prereq
# }
#
# L'avaluació és DETERMINISTA: `tutor._process_prereq_turn` comprova
# substring match sense word-boundary (cas-insensitive). NO crida la IA.
# Aquest disseny és intencional: els prereqs són preguntes molt acotades
# (sí/no, una paraula clau, un valor numèric...) i la IA no aporta res
# a un cost no menor.
PREREQUISITES = {
    "PRE-ESPAI": {
        "id": "PRE-ESPAI",
        "concept": "def_espai_mostral",
        "question": {
            "ca": "Quants resultats té l'espai mostral de llançar dues monedes? Llista'ls.",
            "en": "How many outcomes are in the sample space of flipping two coins? List them.",
        },
        "keywords_required": ["4", "quatre", "four", "hh", "ht", "th", "tt"],
        "forbidden_keywords": ["3", "tres"],
        "explanation": {
            "ca": (
                "L'espai mostral de llançar dues monedes té 4 resultats equiprobables: "
                "{HH, HT, TH, TT}. La grandària és 2² perquè cada moneda té 2 resultats "
                "i les llançades són independents."
            ),
            "en": (
                "The sample space of flipping two coins has 4 equiprobable outcomes: "
                "{HH, HT, TH, TT}. The size is 2² because each coin has 2 outcomes "
                "and the flips are independent."
            ),
        },
    },
    "PRE-COMB": {
        "id": "PRE-COMB",
        "concept": "def_combinatoria",
        "question": {
            "ca": "De quantes maneres pots triar 2 objectes d'un conjunt de 5, sense ordre? (Valor de C(5,2))",
            "en": "How many ways can you choose 2 objects from a set of 5, without order? (Value of C(5,2))",
        },
        "keywords_required": ["10", "deu", "ten"],
        "forbidden_keywords": ["20", "25"],
        "explanation": {
            "ca": (
                "C(5,2) = 5! / (2!·3!) = (5·4) / 2 = 10. "
                "El coeficient binomial C(n,k) compta subconjunts (sense ordre) "
                "de mida k dins d'un conjunt de mida n."
            ),
            "en": (
                "C(5,2) = 5! / (2!·3!) = (5·4) / 2 = 10. "
                "The binomial coefficient C(n,k) counts subsets (unordered) "
                "of size k within a set of size n."
            ),
        },
    },
    "PRE-LAP": {
        "id": "PRE-LAP",
        "concept": "def_laplace",
        "question": {
            "ca": "Quina és la probabilitat de treure un nombre parell tirant un dau equilibrat de 6 cares?",
            "en": "What is the probability of getting an even number rolling a fair 6-sided die?",
        },
        "keywords_required": ["1/2", "0.5", "0,5", "50%", "0.5", "mitja", "half"],
        "forbidden_keywords": ["1/6", "1/3", "2/6"],
        "explanation": {
            "ca": (
                "Casos favorables: {2, 4, 6} → 3 casos. Casos possibles: 6. "
                "P(parell) = 3/6 = 1/2 = 0.5. La regla de Laplace només s'aplica "
                "si tots els casos són equiprobables (dau equilibrat)."
            ),
            "en": (
                "Favourable cases: {2, 4, 6} → 3 cases. Total cases: 6. "
                "P(even) = 3/6 = 1/2 = 0.5. Laplace's rule only applies when "
                "all cases are equiprobable (fair die)."
            ),
        },
    },
    "PRE-COND": {
        "id": "PRE-COND",
        "concept": "def_prob_condicionada",
        "question": {
            "ca": (
                "Com es defineix P(A|B)? Escriu la fórmula que la relaciona amb P(A∩B) i P(B)."
            ),
            "en": (
                "How is P(A|B) defined? Write the formula relating it to P(A∩B) and P(B)."
            ),
        },
        "keywords_required": ["p(a∩b)", "p(a and b)", "p(a^b)", "p(ab)",
                              "intersecci", "intersect", "/", "dividit", "divided"],
        "forbidden_keywords": ["p(b|a)"],
        "explanation": {
            "ca": (
                "P(A|B) = P(A∩B) / P(B), sempre que P(B) > 0. "
                "Es llegeix \"probabilitat de A, sabent que B ha passat\". "
                "Atenció: P(A|B) ≠ P(B|A) en general."
            ),
            "en": (
                "P(A|B) = P(A∩B) / P(B), provided P(B) > 0. "
                "Read as \"probability of A given that B has occurred\". "
                "Beware: P(A|B) ≠ P(B|A) in general."
            ),
        },
    },
    "PRE-IND": {
        "id": "PRE-IND",
        "concept": "def_independencia",
        "question": {
            "ca": (
                "Quan diem que dos successos A i B són independents? Dona la fórmula que ho caracteritza."
            ),
            "en": (
                "When are two events A and B independent? Give the formula that characterizes it."
            ),
        },
        "keywords_required": ["p(a)·p(b)", "p(a)*p(b)", "p(a) · p(b)", "p(a)p(b)",
                              "p(a) p(b)", "producte", "product"],
        "forbidden_keywords": ["p(a)+p(b)", "suma"],
        "explanation": {
            "ca": (
                "A i B són independents ⟺ P(A∩B) = P(A)·P(B). "
                "Equivalentment, P(A|B) = P(A) (saber que B ha passat no canvia la "
                "probabilitat de A). Atenció: independents NO és el mateix que "
                "incompatibles."
            ),
            "en": (
                "A and B are independent ⟺ P(A∩B) = P(A)·P(B). "
                "Equivalently, P(A|B) = P(A) (knowing B occurred does not change "
                "the probability of A). Beware: independent is NOT the same as "
                "mutually exclusive."
            ),
        },
    },
    "PRE-TOT": {
        "id": "PRE-TOT",
        "concept": "def_prob_total",
        "question": {
            "ca": (
                "Si {A₁, A₂} és una partició del espai mostral, com calcules P(B) "
                "a partir de P(B|A₁), P(B|A₂), P(A₁) i P(A₂)?"
            ),
            "en": (
                "If {A₁, A₂} is a partition of the sample space, how do you compute "
                "P(B) from P(B|A₁), P(B|A₂), P(A₁) and P(A₂)?"
            ),
        },
        "keywords_required": ["p(b|a1)·p(a1)", "p(b|a1)*p(a1)", "p(b|a1)p(a1)",
                              "+", "suma", "sum"],
        "forbidden_keywords": ["p(a1|b)", "p(a2|b)"],
        "explanation": {
            "ca": (
                "P(B) = P(B|A₁)·P(A₁) + P(B|A₂)·P(A₂). En un arbre, multipliques "
                "al llarg de cada branca (P(Aᵢ) · P(B|Aᵢ)) i sumes totes les "
                "branques que arriben a B."
            ),
            "en": (
                "P(B) = P(B|A₁)·P(A₁) + P(B|A₂)·P(A₂). On a tree, you multiply "
                "along each branch (P(Aᵢ) · P(B|Aᵢ)) and sum all branches that "
                "reach B."
            ),
        },
    },
    "PRE-BAY": {
        "id": "PRE-BAY",
        "concept": "def_bayes",
        "question": {
            "ca": "Escriu la fórmula de Bayes per a P(A|B) a partir de P(B|A), P(A) i P(B).",
            "en": "Write Bayes' formula for P(A|B) in terms of P(B|A), P(A) and P(B).",
        },
        "keywords_required": ["p(b|a)·p(a)", "p(b|a)*p(a)", "p(b|a)p(a)",
                              "/ p(b)", "/p(b)", "dividit per p(b)"],
        "forbidden_keywords": ["p(a|b)·p(b)", "/ p(a)", "/p(a)"],
        "explanation": {
            "ca": (
                "P(A|B) = P(B|A)·P(A) / P(B). El numerador és la probabilitat "
                "conjunta P(A∩B) reescrita amb la condicionada inversa, i el "
                "denominador P(B) sovint es calcula per la probabilitat total."
            ),
            "en": (
                "P(A|B) = P(B|A)·P(A) / P(B). The numerator is the joint "
                "probability P(A∩B) rewritten via the inverse conditional, and "
                "the denominator P(B) is often computed via total probability."
            ),
        },
    },
    "PRE-BIN": {
        "id": "PRE-BIN",
        "concept": "def_binomial",
        "question": {
            "ca": (
                "Escriu la fórmula de P(X=k) quan X ~ B(n,p). Quins són els tres "
                "factors que la componen?"
            ),
            "en": (
                "Write the formula for P(X=k) when X ~ B(n,p). What are the three "
                "factors that compose it?"
            ),
        },
        "keywords_required": ["c(n,k)", "c(n, k)", "(n k)", "coeficient",
                              "binomial", "p^k", "p**k"],
        "forbidden_keywords": [],
        "explanation": {
            "ca": (
                "P(X=k) = C(n,k) · pᵏ · (1−p)ⁿ⁻ᵏ. Els tres factors són: "
                "(1) el coeficient binomial C(n,k), que compta quantes seqüències "
                "tenen exactament k èxits; (2) pᵏ, la probabilitat dels k èxits; "
                "(3) (1−p)ⁿ⁻ᵏ, la probabilitat dels n−k fracassos."
            ),
            "en": (
                "P(X=k) = C(n,k) · p^k · (1−p)^(n−k). The three factors are: "
                "(1) the binomial coefficient C(n,k), counting how many sequences "
                "have exactly k successes; (2) p^k, the probability of those k "
                "successes; (3) (1−p)^(n−k), the probability of the n−k failures."
            ),
        },
    },
}


# ============================================================
# PROBLEMES PRINCIPALS
# ============================================================
# Cada entrada segueix l'esquema documentat a SCHEMA.md. Les claus
# `expected_summary` són NOMÉS per a la IA — mai s'ensenyen a l'alumne.
PROBLEMS = {

    # ============================================================
    # PROB-PAU-03 — sesamoïditis (PAU 2025-26, problema 3)
    # ============================================================
    # Origen: enunciat literal del recull de problemes PAU del
    # Departament de Matemàtiques (curs 2025-26).
    #
    # Estructura: dos apartats que es resolen seqüencialment.
    #   a) Probabilitat total per obtenir P(S).
    #   b) Bayes per obtenir P(I|S), usant el resultat de (a) com a
    #      denominador.
    #
    # El problema es modelitza com a UN problema del projecte amb 3
    # passos atòmics: identificar dades → calcular P(S) → aplicar
    # Bayes. Cada pas és independentment avaluable; un alumne que
    # falli al pas 2 pot rebre el feedback corresponent sense
    # bloquejar-se els altres dos.
    #
    # Node: PROB-L3-BAY (l'aresta més profunda activada — Bayes).
    "PROB-PAU-03": {
        "id": "PROB-PAU-03",
        "node": "PROB-L3-BAY",
        "familia": "PROB-PAU",
        "nivell": 2,
        "tema": {
            "ca": "PAU — sesamoïditis i esports d'impacte",
            "en": "PAU — sesamoiditis and impact sports",
        },
        "enunciat": {
            "ca": (
                "La lesió per sesamoïditis (inflamació de l'os sesamoide del peu) "
                "és relativament habitual entre la població que practica esports "
                "d'impacte (atletisme, bàsquet, tennis…). En una població d'esportistes, "
                "s'ha fet un estudi diferenciant entre els que practiquen esports "
                "d'impacte i els que practiquen esports sense impacte brusc (com ara "
                "natació, pilates, senderisme…). S'ha pogut determinar que el 45 % "
                "practiquen esports d'impacte. Entre aquests, un 10 % pateixen lesions "
                "per sesamoïditis, mentre que entre els que no practiquen esports "
                "d'impacte només un 3 % presenten aquesta lesió. Escollim un esportista "
                "a l'atzar.\n\n"
                "**a)** Quina és la probabilitat que pateixi sesamoïditis? *[0,75 punts]*\n\n"
                "**b)** Si l'esportista escollit té una lesió per sesamoïditis, quina és "
                "la probabilitat que practiqui esports d'impacte? *[0,75 punts]*"
            ),
            "en": (
                "Sesamoiditis (inflammation of the foot's sesamoid bone) is relatively "
                "common in the population that practices impact sports (athletics, "
                "basketball, tennis…). In a population of athletes, a study has been "
                "carried out distinguishing between those who practice impact sports and "
                "those who practice sports without sharp impact (swimming, pilates, "
                "hiking…). It has been determined that 45% practice impact sports. Among "
                "these, 10% suffer sesamoiditis, while among those who do not practice "
                "impact sports only 3% present this injury. We choose an athlete at "
                "random.\n\n"
                "**a)** What is the probability that they suffer from sesamoiditis? *[0.75 pts]*\n\n"
                "**b)** If the chosen athlete has a sesamoiditis injury, what is the "
                "probability that they practice impact sports? *[0.75 pts]*"
            ),
        },
        "input_mode": "free_text",
        "answer_language": "ca",
        # Dependències que el problema sencer exercita. Reordenades de
        # més bàsica a més avançada per coincidir amb l'ordre de
        # consolidació al DAG.
        "dependencies": [
            "def_prob_condicionada",
            "def_prob_total",
            "def_bayes",
        ],
        # Errors típics. La fórmula de Bayes té tres modes de fallada
        # documentats al catàleg, tots tres rellevants en aquest problema.
        "errors_freqüents": [
            "COND_invertit",
            "TOT_branca_oblidada",
            "TOT_suma_vs_producte",
            "BAY_invertit",
            "BAY_no_prob_total",
        ],
        "passos": [
            # --------------------------------------------------------
            # Pas 1 — identificació de les dades de l'enunciat
            # --------------------------------------------------------
            # Free text perquè l'alumne ha de mostrar comprensió del
            # significat de cada probabilitat, no només els valors.
            # El judge_step accepta variacions raonables: assignar
            # P(I)=0.45 o P(impacte)=0.45 o P(impact)=0.45 són tots OK.
            {
                "id": 1,
                "text": {
                    "ca": (
                        "**Apartat a)** Defineix els successos rellevants i identifica les "
                        "quatre probabilitats que dóna l'enunciat. Suggeriment: usa "
                        "I = «practica esports d'impacte», S = «pateix sesamoïditis»."
                    ),
                    "en": (
                        "**Part a)** Define the relevant events and identify the four "
                        "probabilities given by the problem. Hint: use "
                        "I = \"practices impact sports\", S = \"suffers sesamoiditis\"."
                    ),
                },
                "expected_summary": (
                    "P(I) = 0.45, P(Ī) = 0.55, P(S|I) = 0.10, P(S|Ī) = 0.03. "
                    "Atenció: l'alumne ha de notar que P(S|I) és la probabilitat "
                    "condicionada a la pràctica d'esport d'impacte, no a l'inrevés."
                ),
                "key_concepts": ["def_prob_condicionada"],
                "input_type": "free_text",
                "expected_value": None,
                "typical_error": (
                    "confusing P(S|I) with P(I|S) (inverted conditional probability), "
                    "or assigning P(Ī) = 0.45 instead of 0.55"
                ),
                "typical_error_label": "COND_invertit",
            },
            # --------------------------------------------------------
            # Pas 2 — probabilitat total per a P(S)
            # --------------------------------------------------------
            # Decimal amb tolerància 1e-4. El valor exacte és 0.0615;
            # qualsevol resposta entre 0.0614 i 0.0616 es considera
            # correcta. Una resposta de 0.045 (oblidant la branca Ī)
            # quedaria etiquetada com a TOT_branca_oblidada.
            {
                "id": 2,
                "text": {
                    "ca": (
                        "**Apartat a)** Aplica la fórmula de la probabilitat total per "
                        "calcular P(S), la probabilitat que un esportista escollit a "
                        "l'atzar pateixi sesamoïditis. Dona el resultat com a decimal "
                        "(4 xifres després de la coma)."
                    ),
                    "en": (
                        "**Part a)** Apply the total probability theorem to compute P(S), "
                        "the probability that a randomly chosen athlete suffers "
                        "sesamoiditis. Give the answer as a decimal (4 decimal places)."
                    ),
                },
                "expected_summary": (
                    "P(S) = P(S|I)·P(I) + P(S|Ī)·P(Ī) = 0.10·0.45 + 0.03·0.55 "
                    "= 0.045 + 0.0165 = 0.0615"
                ),
                "key_concepts": ["def_prob_total"],
                "input_type": "decimal",
                "expected_value": 0.0615,
                "typical_error": (
                    "omitting the second branch (giving only 0.045 = 0.10·0.45), "
                    "or adding probabilities along a branch instead of multiplying"
                ),
                "typical_error_label": "TOT_branca_oblidada",
            },
            # --------------------------------------------------------
            # Pas 3 — Bayes per a P(I|S)
            # --------------------------------------------------------
            # Fraction amb expected_value = "30/41". L'alumne pot
            # respondre en forma de fracció ("30/41", "450/615") o
            # decimal ("0.7317", "0.732"). El verificador les accepta
            # totes via Fraction().limit_denominator(10_000).
            #
            # Càlcul: 0.045 / 0.0615 = 450/615 = 30/41 ≈ 0.7317.
            # 41 és primer; gcd(30,41)=1, així que 30/41 és la fracció
            # totalment reduïda.
            {
                "id": 3,
                "text": {
                    "ca": (
                        "**Apartat b)** Aplica la fórmula de Bayes per calcular P(I|S), la "
                        "probabilitat que un esportista amb sesamoïditis practiqui esports "
                        "d'impacte. Dóna el resultat com a fracció reduïda o decimal amb "
                        "4 xifres."
                    ),
                    "en": (
                        "**Part b)** Apply Bayes' formula to find P(I|S), the probability "
                        "that an athlete with sesamoiditis practices impact sports. Give "
                        "the answer as a reduced fraction or a 4-digit decimal."
                    ),
                },
                "expected_summary": (
                    "P(I|S) = P(S|I)·P(I) / P(S) = (0.10·0.45) / 0.0615 = 0.045 / 0.0615 "
                    "= 450/615 = 30/41 ≈ 0.7317"
                ),
                "key_concepts": ["def_bayes"],
                "input_type": "fraction",
                "expected_value": "30/41",
                "typical_error": (
                    "inverting the Bayes formula (placing P(S|I) in the denominator "
                    "or P(I) in the numerator without multiplying)"
                ),
                "typical_error_label": "BAY_invertit",
            },
        ],
    },
}


# Ordre recomanat del camí pilot (topològic en el DAG)
# Camí actiu del pilot. Conté un sol problema (PROB-PAU-03) com a
# prototip dels problemes PAU. Quan validem el format, s'hi afegiran
# els altres 5 problemes del recull PAU 2025-26.
PILOT_PATH = [
    "PROB-PAU-03",
]


# ---------- Accessors ----------
def get_problem(problem_id: str) -> dict:
    p = PROBLEMS.get(problem_id)
    if p is None:
        raise KeyError(f"Problema '{problem_id}' no trobat.")
    return p


def get_dependency(dep_id: str) -> dict:
    return DEPENDENCIES.get(dep_id)


def get_prerequisite(prereq_id: str) -> dict:
    return PREREQUISITES.get(prereq_id)


def list_problems():
    return list(PROBLEMS.keys())
