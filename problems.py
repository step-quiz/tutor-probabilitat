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


def get_localized(field, lang: str) -> str:
    """
    Aplana un camp bilingüe a un string en la llengua demanada.

    Accepta:
    - `dict` de la forma `{"ca": "...", "en": "..."}` → retorna l'entrada
      corresponent a `lang`, amb fallback a "en" i, si tampoc hi és, al
      primer valor present.
    - `str` simple → es retorna sense modificar (camps legacy o no
      bilingües, com ara els ids o els valors de keywords).

    Garanteix que mai retornarà None: si tot falla, retorna string buit.
    """
    if isinstance(field, dict):
        return field.get(lang) or field.get("en") or next(iter(field.values()), "")
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

    # ---- PROB-L1-LAP — dau equilibrat ----
    "PROB-LAP-01": {
        "id": "PROB-LAP-01",
        "node": "PROB-L1-LAP",
        "familia": "PROB-LAP",
        "nivell": 1,
        "tema": {
            "ca": "Laplace — un dau equilibrat",
            "en": "Laplace — a fair die",
        },
        "enunciat": {
            "ca": (
                "Es llança un dau equilibrat de 6 cares. "
                "Quina és la probabilitat d'obtenir un múltiple de 3?"
            ),
            "en": (
                "A fair 6-sided die is rolled. "
                "What is the probability of getting a multiple of 3?"
            ),
        },
        "input_mode": "free_text",
        "answer_language": "ca",
        "dependencies": ["def_espai_mostral", "def_laplace"],
        "errors_freqüents": ["LAP_favorable_total_swap", "GEN_arithmetic"],
        "passos": [
            {
                "id": 1,
                "text": {
                    "ca": "Quants casos favorables hi ha? (Quants múltiples de 3 entre 1 i 6.)",
                    "en": "How many favourable cases are there? (How many multiples of 3 between 1 and 6.)",
                },
                "expected_summary": "Favourable cases: {3, 6} → 2 cases.",
                "key_concepts": ["def_espai_mostral"],
                "input_type": "integer",
                "expected_value": 2,
                "typical_error": "miscounting: claims 1 (only 6) or 3 (including 9)",
                "typical_error_label": "LAP_doble_recompte",
            },
            {
                "id": 2,
                "text": {
                    "ca": "Aplica la regla de Laplace per obtenir la probabilitat. Escriu el resultat com a fracció.",
                    "en": "Apply Laplace's rule to get the probability. Write the result as a fraction.",
                },
                "expected_summary": "P(múltiple de 3) = 2/6 = 1/3",
                "key_concepts": ["def_laplace"],
                "input_type": "fraction",
                "expected_value": "1/3",
                "typical_error": "inverting favourable and total cases (6/2 instead of 2/6)",
                "typical_error_label": "LAP_favorable_total_swap",
            },
        ],
    },

    # ---- PROB-L1-LAP — comptatge (combinatòria lleugera) ----
    "PROB-LAP-02": {
        "id": "PROB-LAP-02",
        "node": "PROB-L1-LAP",
        "familia": "PROB-LAP",
        "nivell": 2,
        "tema": {
            "ca": "Laplace — extreure dues boles",
            "en": "Laplace — drawing two balls",
        },
        "enunciat": {
            "ca": (
                "Una urna conté 4 boles vermelles i 3 boles blaves. "
                "S'extreuen 2 boles a l'atzar, sense reemplaçament. "
                "Quina és la probabilitat que totes dues siguin vermelles?"
            ),
            "en": (
                "An urn contains 4 red balls and 3 blue balls. "
                "Two balls are drawn at random, without replacement. "
                "What is the probability that both are red?"
            ),
        },
        "input_mode": "free_text",
        "answer_language": "ca",
        "dependencies": ["def_combinatoria", "def_laplace"],
        "errors_freqüents": ["BIN_n_k_invertits", "LAP_doble_recompte", "GEN_arithmetic"],
        "passos": [
            {
                "id": 1,
                "text": {
                    "ca": "Quants casos possibles hi ha en total? (Maneres de triar 2 boles de 7 sense ordre.)",
                    "en": "How many total cases are there? (Ways to choose 2 balls from 7 without order.)",
                },
                "expected_summary": "C(7,2) = 21",
                "key_concepts": ["def_combinatoria"],
                "input_type": "integer",
                "expected_value": 21,
                "typical_error": "computing C(7,2) wrong (e.g., 7·6 = 42 with order, or 7·2 = 14)",
                "typical_error_label": "BIN_n_k_invertits",
            },
            {
                "id": 2,
                "text": {
                    "ca": "Quants casos favorables hi ha? (Maneres de triar 2 boles vermelles entre 4.)",
                    "en": "How many favourable cases are there? (Ways to choose 2 red balls from 4.)",
                },
                "expected_summary": "C(4,2) = 6",
                "key_concepts": ["def_combinatoria"],
                "input_type": "integer",
                "expected_value": 6,
                "typical_error": "miscounting C(4,2)",
                "typical_error_label": "LAP_doble_recompte",
            },
            {
                "id": 3,
                "text": {
                    "ca": "Aplica la regla de Laplace. Expressa la probabilitat com a fracció reduïda.",
                    "en": "Apply Laplace's rule. Express the probability as a reduced fraction.",
                },
                "expected_summary": "P(2 vermelles) = 6/21 = 2/7",
                "key_concepts": ["def_laplace"],
                "input_type": "fraction",
                "expected_value": "2/7",
                "typical_error": "leaving the fraction unreduced or inverting favourable/total",
                "typical_error_label": "LAP_favorable_total_swap",
            },
        ],
    },

    # ---- PROB-L2-TOT — dues causes ----
    "PROB-TOT-01": {
        "id": "PROB-TOT-01",
        "node": "PROB-L2-TOT",
        "familia": "PROB-TOT",
        "nivell": 2,
        "tema": {
            "ca": "Probabilitat total — dues urnes",
            "en": "Total probability — two urns",
        },
        "enunciat": {
            "ca": (
                "Hi ha dues urnes. L'urna U1 conté 3 boles blanques i 7 negres. "
                "L'urna U2 conté 6 boles blanques i 4 negres. "
                "Es tria una urna a l'atzar (cada urna amb probabilitat 1/2) i "
                "se n'extreu una bola. Quina és la probabilitat que sigui blanca?"
            ),
            "en": (
                "There are two urns. Urn U1 contains 3 white balls and 7 black ones. "
                "Urn U2 contains 6 white balls and 4 black ones. "
                "An urn is chosen at random (each with probability 1/2) and a "
                "ball is drawn. What is the probability it is white?"
            ),
        },
        "input_mode": "free_text",
        "answer_language": "ca",
        "dependencies": [
            "def_prob_condicionada",
            "def_prob_total",
        ],
        "errors_freqüents": [
            "TOT_branca_oblidada",
            "TOT_suma_vs_producte",
            "COND_invertit",
        ],
        "passos": [
            {
                "id": 1,
                "text": {
                    "ca": (
                        "Identifica les probabilitats que et dóna l'enunciat: "
                        "P(U1), P(U2), P(B|U1) i P(B|U2), on B = «la bola és blanca»."
                    ),
                    "en": (
                        "Identify the probabilities given in the problem: "
                        "P(U1), P(U2), P(B|U1) and P(B|U2), where B = \"the ball is white\"."
                    ),
                },
                "expected_summary": "P(U1)=1/2, P(U2)=1/2, P(B|U1)=3/10, P(B|U2)=6/10",
                "key_concepts": ["def_prob_condicionada"],
                "input_type": "free_text",
                "expected_value": None,
                "typical_error": "confusing P(B|U1) with P(U1|B)",
                "typical_error_label": "COND_invertit",
            },
            {
                "id": 2,
                "text": {
                    "ca": "Calcula la contribució de la branca U1: P(U1)·P(B|U1).",
                    "en": "Compute the U1 branch contribution: P(U1)·P(B|U1).",
                },
                "expected_summary": "(1/2)·(3/10) = 3/20 = 0.15",
                "key_concepts": ["def_prob_total"],
                "input_type": "decimal",
                "expected_value": 0.15,
                "typical_error": "adding instead of multiplying along the branch",
                "typical_error_label": "TOT_suma_vs_producte",
            },
            {
                "id": 3,
                "text": {
                    "ca": (
                        "Aplica el teorema de la probabilitat total per obtenir P(B). "
                        "Suma les contribucions de totes les branques."
                    ),
                    "en": (
                        "Apply the total probability theorem to get P(B). "
                        "Sum the contributions of all branches."
                    ),
                },
                "expected_summary": (
                    "P(B) = (1/2)·(3/10) + (1/2)·(6/10) = 0.15 + 0.30 = 0.45"
                ),
                "key_concepts": ["def_prob_total"],
                "input_type": "decimal",
                "expected_value": 0.45,
                "typical_error": "omitting one branch of the total probability expansion",
                "typical_error_label": "TOT_branca_oblidada",
            },
        ],
    },

    # ---- PROB-L3-BAY — exemple complet del briefing ----
    "PROB-BAY-01": {
        "id": "PROB-BAY-01",
        "node": "PROB-L3-BAY",
        "familia": "PROB-BAY",
        "nivell": 2,
        "tema": {
            "ca": "Bayes — dues màquines",
            "en": "Bayes — two machines",
        },
        "enunciat": {
            "ca": (
                "Una fàbrica produeix peces amb dues màquines. "
                "M1 produeix el 60 % de les peces i té un 3 % de defectes. "
                "M2 produeix el 40 % restant i té un 5 % de defectes. "
                "Se selecciona una peça a l'atzar i resulta defectuosa. "
                "Quina és la probabilitat que hagi estat produïda per M1?"
            ),
            "en": (
                "A factory produces parts with two machines. "
                "M1 produces 60 % of parts with a 3 % defect rate. "
                "M2 produces the remaining 40 % with a 5 % defect rate. "
                "A part is chosen at random and found to be defective. "
                "What is the probability it was produced by M1?"
            ),
        },
        "input_mode": "free_text",
        "answer_language": "ca",
        "dependencies": [
            "def_prob_condicionada",
            "def_prob_total",
            "def_bayes",
        ],
        "errors_freqüents": [
            "BAY_invertit",
            "BAY_no_prob_total",
            "BAY_confon_conjunta",
        ],
        "passos": [
            {
                "id": 1,
                "text": {
                    "ca": (
                        "Identifica les quatre probabilitats que et dóna l'enunciat: "
                        "P(M1), P(M2), P(D|M1) i P(D|M2)."
                    ),
                    "en": (
                        "Identify the four probabilities given by the problem: "
                        "P(M1), P(M2), P(D|M1) and P(D|M2)."
                    ),
                },
                "expected_summary": "P(M1)=0.6, P(M2)=0.4, P(D|M1)=0.03, P(D|M2)=0.05",
                "key_concepts": ["def_prob_condicionada"],
                "input_type": "free_text",
                "expected_value": None,
                "typical_error": "confusing P(D|M1) with P(M1|D)",
                "typical_error_label": "BAY_invertit",
            },
            {
                "id": 2,
                "text": {
                    "ca": "Calcula P(D), la probabilitat total d'obtenir una peça defectuosa.",
                    "en": "Compute P(D), the total probability of getting a defective part.",
                },
                "expected_summary": "P(D) = 0.6·0.03 + 0.4·0.05 = 0.018 + 0.020 = 0.038",
                "key_concepts": ["def_prob_total"],
                "input_type": "decimal",
                "expected_value": 0.038,
                "typical_error": "omitting one branch of the total probability expansion",
                "typical_error_label": "TOT_branca_oblidada",
            },
            {
                "id": 3,
                "text": {
                    "ca": "Aplica el teorema de Bayes per obtenir P(M1|D).",
                    "en": "Apply Bayes' theorem to find P(M1|D).",
                },
                "expected_summary": (
                    "P(M1|D) = P(D|M1)·P(M1) / P(D) = 0.018 / 0.038 = 9/19 ≈ 0.4737"
                ),
                "key_concepts": ["def_bayes"],
                "input_type": "fraction",
                "expected_value": "9/19",
                "typical_error": "inverting numerator and denominator in Bayes formula",
                "typical_error_label": "BAY_invertit",
            },
        ],
    },

    # ---- PROB-L4-BIN — exacte P(X=k) ----
    "PROB-BIN-01": {
        "id": "PROB-BIN-01",
        "node": "PROB-L4-BIN",
        "familia": "PROB-BIN",
        "nivell": 2,
        "tema": {
            "ca": "Binomial — exactament k èxits",
            "en": "Binomial — exactly k successes",
        },
        "enunciat": {
            "ca": (
                "Un examen té 10 preguntes tipus test, cadascuna amb 4 opcions i una "
                "única correcta. Un alumne respon totes les preguntes a l'atzar. "
                "Quina és la probabilitat d'encertar EXACTAMENT 3 preguntes?"
            ),
            "en": (
                "A test has 10 multiple-choice questions, each with 4 options and one "
                "correct answer. A student answers all questions at random. "
                "What is the probability of getting EXACTLY 3 questions right?"
            ),
        },
        "input_mode": "free_text",
        "answer_language": "ca",
        "dependencies": [
            "def_combinatoria",
            "def_binomial",
        ],
        "errors_freqüents": [
            "BIN_n_k_invertits",
            "BIN_p_vs_q",
            "GEN_arithmetic",
        ],
        "passos": [
            {
                "id": 1,
                "text": {
                    "ca": "Identifica n, k i p per a la binomial X ~ B(n, p). Quin és n?",
                    "en": "Identify n, k and p for the binomial X ~ B(n, p). What is n?",
                },
                "expected_summary": "n = 10 (number of questions / trials)",
                "key_concepts": ["def_binomial"],
                "input_type": "integer",
                "expected_value": 10,
                "typical_error": "confusing n with k (e.g., answering 3)",
                "typical_error_label": "BIN_n_k_invertits",
            },
            {
                "id": 2,
                "text": {
                    "ca": "Calcula el coeficient binomial C(10, 3).",
                    "en": "Compute the binomial coefficient C(10, 3).",
                },
                "expected_summary": "C(10,3) = 120",
                "key_concepts": ["def_combinatoria"],
                "input_type": "integer",
                "expected_value": 120,
                "typical_error": "swapping n and k (computing C(3,10) which is 0)",
                "typical_error_label": "BIN_n_k_invertits",
            },
            {
                "id": 3,
                "text": {
                    "ca": (
                        "Aplica la fórmula P(X=3) = C(10,3) · p³ · (1−p)⁷ amb p = 1/4. "
                        "Dona el resultat amb 4 decimals."
                    ),
                    "en": (
                        "Apply the formula P(X=3) = C(10,3) · p^3 · (1−p)^7 with p = 1/4. "
                        "Give the result to 4 decimal places."
                    ),
                },
                "expected_summary": (
                    "P(X=3) = 120 · (1/4)³ · (3/4)⁷ = 120 · (1/64) · (2187/16384) "
                    "= 262440/1048576 ≈ 0.2503"
                ),
                "key_concepts": ["def_binomial"],
                "input_type": "decimal",
                "expected_value": 0.2503,
                "typical_error": "using p = 3/4 instead of 1/4 (success vs failure)",
                "typical_error_label": "BIN_p_vs_q",
            },
        ],
    },

    # ---- PROB-L4-BIN — complementari P(X≥k) ----
    "PROB-BIN-02": {
        "id": "PROB-BIN-02",
        "node": "PROB-L4-BIN",
        "familia": "PROB-BIN",
        "nivell": 3,
        "tema": {
            "ca": "Binomial — almenys 1 èxit (complementari)",
            "en": "Binomial — at least 1 success (complement)",
        },
        "enunciat": {
            "ca": (
                "Una jugadora de bàsquet encerta el 70 % dels seus tirs lliures. "
                "Si llança 5 tirs lliures independents, quina és la probabilitat "
                "que encerti AL MENYS UN?"
            ),
            "en": (
                "A basketball player makes 70 % of her free throws. "
                "If she takes 5 independent free throws, what is the probability "
                "that she makes AT LEAST ONE?"
            ),
        },
        "input_mode": "free_text",
        "answer_language": "ca",
        "dependencies": [
            "def_binomial",
            "def_independencia",
        ],
        "errors_freqüents": [
            "BIN_complementari_oblidat",
            "BIN_p_vs_q",
            "BIN_n_k_invertits",
        ],
        "passos": [
            {
                "id": 1,
                "text": {
                    "ca": (
                        "Per què és més pràctic calcular P(X≥1) usant el complementari "
                        "P(X=0)? Explica la idea breument."
                    ),
                    "en": (
                        "Why is it more practical to compute P(X≥1) using the complement "
                        "P(X=0)? Briefly explain the idea."
                    ),
                },
                "expected_summary": (
                    "P(X≥1) = 1 − P(X=0). Calcular P(X≥1) directament requereix sumar "
                    "P(X=1)+...+P(X=5) (cinc termes); el complementari només requereix un."
                ),
                "key_concepts": ["def_binomial"],
                "input_type": "free_text",
                "expected_value": None,
                "typical_error": "tries to sum P(X=1)+...+P(X=5) directly without realizing P(X≥1) = 1−P(X=0)",
                "typical_error_label": "BIN_complementari_oblidat",
            },
            {
                "id": 2,
                "text": {
                    "ca": (
                        "Calcula P(X=0) amb la fórmula binomial (n=5, p=0.7). "
                        "Expressa el resultat com a fracció exacta o decimal amb 4 xifres."
                    ),
                    "en": (
                        "Compute P(X=0) using the binomial formula (n=5, p=0.7). "
                        "Express the result as an exact fraction or decimal with 4 digits."
                    ),
                },
                "expected_summary": (
                    "P(X=0) = C(5,0) · (0.7)⁰ · (0.3)⁵ = 1 · 1 · 0.00243 = 0.00243"
                ),
                "key_concepts": ["def_binomial"],
                "input_type": "fraction",
                "expected_value": "243/100000",
                "typical_error": "using p=0.3 (failure) where q=0.3 is needed in (1−p)^n, getting (0.7)^5 instead",
                "typical_error_label": "BIN_p_vs_q",
            },
            {
                "id": 3,
                "text": {
                    "ca": "Calcula P(X≥1) restant del total. Dona el resultat amb 5 decimals.",
                    "en": "Compute P(X≥1) by subtracting from the total. Give the result to 5 decimal places.",
                },
                "expected_summary": "P(X≥1) = 1 − 0.00243 = 0.99757",
                "key_concepts": ["def_binomial"],
                "input_type": "fraction",
                "expected_value": "99757/100000",
                "typical_error": "forgetting to subtract from 1 (giving 0.00243 as the answer)",
                "typical_error_label": "BIN_complementari_oblidat",
            },
        ],
    },
}


# Ordre recomanat del camí pilot (topològic en el DAG)
PILOT_PATH = [
    "PROB-LAP-01",
    "PROB-LAP-02",
    "PROB-TOT-01",
    "PROB-BAY-01",
    "PROB-BIN-01",
    "PROB-BIN-02",
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
