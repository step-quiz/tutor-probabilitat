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
    # ---- Succés complementari ----
    "COMP_no_complement": {
        "ca": "No fa servir el complementari quan és la via més curta (calcula directament en comptes de P(...)=1−P(no...))",
        "en": "Does not use the complement when it is the shortest path (computes directly instead of P(...)=1−P(not...))",
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
    # Succés complementari
    "COMP_no_complement":        "def_succes_complementari",
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
    "def_succes_complementari": {
        "description": {
            "ca": "succés complementari: P(Ā) = 1 − P(A); útil per a «almenys un» o «com a mínim k»",
            "en": "complementary event: P(Ā) = 1 − P(A); useful for 'at least one' or 'at least k'",
        },
        "keywords": ["complementari", "complement", "complementary",
                     "almenys", "al menys", "at least", "1 -", "1-", "1 −"],
        "prerequisite": "PRE-COMP",
        "dag_node": "PROB-L1-LAP",
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
    "PRE-COMP": {
        "id": "PRE-COMP",
        "concept": "def_succes_complementari",
        "question": {
            "ca": (
                "Si P(A) = 0,3, quant val P(Ā), la probabilitat que NO passi A?"
            ),
            "en": (
                "If P(A) = 0.3, what is P(Ā), the probability that A does NOT occur?"
            ),
        },
        "keywords_required": ["0.7", "0,7", "7/10", "70%", "0.70", "0,70"],
        "forbidden_keywords": ["0.3", "0,3", "3/10", "0.4", "0,4"],
        "explanation": {
            "ca": (
                "P(Ā) = 1 − P(A) = 1 − 0,3 = 0,7. El complementari és tot el que "
                "queda fora de A dins l'espai mostral. És una eina clau per a "
                "preguntes com «almenys un» o «com a mínim k»: sovint P(almenys un) "
                "= 1 − P(cap) és molt més curt que sumar termes."
            ),
            "en": (
                "P(Ā) = 1 − P(A) = 1 − 0.3 = 0.7. The complement is everything "
                "outside A in the sample space. It is a key tool for 'at least one' "
                "or 'at least k' questions: P(at least one) = 1 − P(none) is often "
                "much shorter than summing terms."
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
    # PROB-PAU-01 — peces de ferro/acer + demostració binomial (PAU 2025-26, problema 1)
    # ============================================================
    # Origen: enunciat literal del recull de problemes PAU del
    # Departament de Matemàtiques (curs 2025-26).
    #
    # Estructura: dos apartats independents al mateix problema.
    #   a) Probabilitat total per obtenir P(D).
    #   b) DEMOSTRACIÓ algèbrica que f(p) = 5(p⁴ − p⁵) per a P(X=4)
    #      amb X ~ Bin(5, p). L'alumne pot derivar-ho via la fórmula
    #      binomial o per un argument combinatori directe (5 maneres
    #      d'escollir quina és la peça bona).
    #
    # Decomposició en 3 passos:
    #   pas 1: identificar les dades de l'enunciat (free_text)
    #   pas 2: calcular P(D) = 0.042 (decimal)
    #   pas 3: demostrar f(p) = 5(p⁴ − p⁵) (free_text — la IA jutja
    #          el raonament, no un valor numèric)
    #
    # Node: PROB-L4-BIN (la demostració binomial és el punt més
    # profund activat). Nivell 3 perquè conté una derivació.
    "PROB-PAU-01": {
        "id": "PROB-PAU-01",
        "node": "PROB-L4-BIN",
        "familia": "PROB-PAU",
        "nivell": 3,
        "tema": {
            "ca": "PAU — peces de ferro/acer i demostració binomial",
            "en": "PAU — iron/steel parts and binomial demonstration",
        },
        "enunciat": {
            "ca": (
                "Una empresa produeix dos tipus de peces, de ferro i d'acer. "
                "El 60 % de la producció total correspon a peces de ferro i la "
                "resta són d'acer. Sabem que el 95 % de les peces de ferro "
                "produïdes no tenen cap defecte, mentre que el 3 % de les peces "
                "d'acer són defectuoses.\n\n"
                "**a)** Si agafem una peça a l'atzar, quina és la probabilitat "
                "que sigui defectuosa? *[0,75 punts]*\n\n"
                "**b)** L'empresa aviat diversificarà la producció i començarà "
                "a produir també peces de titani, que es vendran en paquets de 5. "
                "Si la probabilitat que una peça de titani sigui defectuosa és "
                "un valor desconegut *p*, i cada peça és defectuosa independentment "
                "de les altres, comproveu que l'expressió que ens dona la "
                "probabilitat que en un paquet de 5 peces n'hi hagi exactament 4 "
                "de defectuoses (en funció de *p*) és **f(p) = 5(p⁴ − p⁵)**. "
                "*[0,75 punts]*"
            ),
            "en": (
                "A company produces two kinds of parts, iron and steel. 60% of "
                "total production is iron parts, the rest is steel. 95% of iron "
                "parts have no defects, while 3% of steel parts are defective.\n\n"
                "**a)** If we pick a random part, what is the probability that it "
                "is defective? *[0.75 pts]*\n\n"
                "**b)** The company will soon start producing titanium parts, "
                "sold in packs of 5. If the probability that a titanium part is "
                "defective is an unknown value *p*, and each part is defective "
                "independently of the others, prove that the expression giving the "
                "probability of exactly 4 defective parts in a pack (as a function "
                "of *p*) is **f(p) = 5(p⁴ − p⁵)**. *[0.75 pts]*"
            ),
        },
        "input_mode": "free_text",
        "answer_language": "ca",
        "dependencies": [
            "def_prob_condicionada",
            "def_prob_total",
            "def_binomial",
            "def_independencia",
        ],
        "errors_freqüents": [
            "COND_invertit",
            "TOT_branca_oblidada",
            "TOT_suma_vs_producte",
            "BIN_n_k_invertits",
            "BIN_p_vs_q",
            "IND_assumida",
        ],
        "passos": [
            # --------------------------------------------------------
            # Pas 1 — identificació de les dades de l'apartat a)
            # --------------------------------------------------------
            # Trampa típica: l'alumne pot oblidar que ferro és el 60 %
            # (no 40 %), o invertir P(D|F) amb P(F|D).
            {
                "id": 1,
                "text": {
                    "ca": (
                        "**Apartat a)** Defineix els successos rellevants i identifica "
                        "les quatre probabilitats que dóna l'enunciat. Suggeriment: "
                        "usa F = «peça de ferro», A = «peça d'acer», D = «peça "
                        "defectuosa»."
                    ),
                    "en": (
                        "**Part a)** Define the relevant events and identify the four "
                        "probabilities given by the problem. Hint: use F = \"iron "
                        "part\", A = \"steel part\", D = \"defective part\"."
                    ),
                },
                "expected_summary": (
                    "P(F) = 0.6, P(A) = 1 − 0.6 = 0.4, P(D|F) = 0.05 (perquè el "
                    "95 % de les peces de ferro NO tenen defectes), P(D|A) = 0.03. "
                    "Atenció: l'alumne ha de complementar el 95 % a 5 % per obtenir "
                    "P(D|F), i no confondre P(D|F) amb P(F|D)."
                ),
                "key_concepts": ["def_prob_condicionada"],
                "input_type": "free_text",
                "expected_value": None,
                "typical_error": (
                    "using P(D|F) = 0.95 instead of 0.05 (forgetting to complement "
                    "the 'no defects' percentage), or inverting P(D|F) with P(F|D)"
                ),
                "typical_error_label": "COND_invertit",
            },
            # --------------------------------------------------------
            # Pas 2 — probabilitat total per a P(D)
            # --------------------------------------------------------
            # Tolerància 1e-4 sobre 0.042. Una resposta de 0.030 (només
            # branca ferro) o 0.012 (només branca acer) quedaria
            # etiquetada com a TOT_branca_oblidada.
            {
                "id": 2,
                "text": {
                    "ca": (
                        "**Apartat a)** Aplica la fórmula de la probabilitat total "
                        "per calcular P(D), la probabilitat que una peça escollida "
                        "a l'atzar sigui defectuosa. Dóna el resultat com a decimal "
                        "(4 xifres després de la coma)."
                    ),
                    "en": (
                        "**Part a)** Apply the total probability theorem to compute "
                        "P(D), the probability that a randomly chosen part is "
                        "defective. Give the answer as a decimal (4 decimal places)."
                    ),
                },
                "expected_summary": (
                    "P(D) = P(D|F)·P(F) + P(D|A)·P(A) = 0.05·0.6 + 0.03·0.4 "
                    "= 0.030 + 0.012 = 0.042"
                ),
                "key_concepts": ["def_prob_total"],
                "input_type": "decimal",
                "expected_value": 0.042,
                "typical_error": (
                    "omitting one of the two branches (giving 0.030 or 0.012), or "
                    "adding probabilities along a branch instead of multiplying"
                ),
                "typical_error_label": "TOT_branca_oblidada",
            },
            # --------------------------------------------------------
            # Pas 3 — demostració de f(p) = 5(p⁴ − p⁵)
            # --------------------------------------------------------
            # Pas free_text perquè és una derivació, no un valor.
            # Hi ha dues vies vàlides (fórmula binomial / argument
            # combinatori). El judge accepta qualsevol de les dues
            # mentre arribi a 5p⁴(1−p) = 5(p⁴−p⁵).
            #
            # Error típic principal: BIN_n_k_invertits (escriure C(4,5)
            # en comptes de C(5,4)). Coincidentment C(5,4)=C(5,1)=5, així
            # que l'alumne pot intuir el 5 sense entendre el coeficient.
            {
                "id": 3,
                "text": {
                    "ca": (
                        "**Apartat b)** Demostra que f(p) = 5(p⁴ − p⁵) és la "
                        "probabilitat que un paquet de 5 peces de titani contingui "
                        "exactament 4 peces defectuoses, en funció de *p*. Pots "
                        "usar la fórmula binomial o un argument combinatori directe; "
                        "indica clarament n, k i p."
                    ),
                    "en": (
                        "**Part b)** Prove that f(p) = 5(p⁴ − p⁵) is the probability "
                        "of exactly 4 defective parts in a pack of 5 titanium parts, "
                        "as a function of *p*. You may use the binomial formula or a "
                        "direct combinatorial argument; clearly state n, k and p."
                    ),
                },
                "expected_summary": (
                    "Via 1 (fórmula binomial): X ∼ B(n=5, p). "
                    "P(X=4) = C(5,4)·p⁴·(1−p)¹ = 5·p⁴·(1−p) = 5p⁴ − 5p⁵ = 5(p⁴ − p⁵). "
                    "Via 2 (argument combinatori directe): la probabilitat d'una "
                    "seqüència concreta amb 4 defectuoses i 1 bona és p⁴·(1−p). "
                    "Com que hi ha C(5,1)=5 maneres d'escollir quina és la peça bona, "
                    "P(X=4) = 5·p⁴·(1−p) = 5(p⁴−p⁵)."
                ),
                "key_concepts": ["def_binomial", "def_independencia"],
                "input_type": "free_text",
                "expected_value": None,
                "typical_error": (
                    "swapping n and k in the binomial coefficient (C(4,5) instead "
                    "of C(5,4)), or omitting the (1−p) factor for the one good part"
                ),
                "typical_error_label": "BIN_n_k_invertits",
            },
        ],
    },

    # ============================================================
    # PROB-PAU-02 — filtre de correu brossa (PAU 2025-26, problema 2)
    # ============================================================
    # Origen: enunciat literal del recull de problemes PAU del
    # Departament de Matemàtiques (curs 2025-26).
    #
    # Estructura paral·lela a PAU-03 (probabilitat total → Bayes).
    # SUBTILESA: l'enunciat dóna P(safata_entrada | B̄) = 0.90, NO
    # directament P(CB | B̄). L'alumne ha de complementar a 0.10 per
    # poder aplicar la probabilitat total. És una variant del mateix
    # patró que a PAU-01 pas 1 (complementar el "no defectuoses").
    #
    # Decomposició en 3 passos:
    #   pas 1: identificar les dades (free_text)
    #   pas 2: P(CB) per probabilitat total = 0.3125 (decimal)
    #   pas 3: P(B̄|CB) per Bayes = 6/25 = 0.24 (fraction)
    #
    # Node: PROB-L3-BAY. Nivell 2 (estructura idèntica a PAU-03).
    "PROB-PAU-02": {
        "id": "PROB-PAU-02",
        "node": "PROB-L3-BAY",
        "familia": "PROB-PAU",
        "nivell": 2,
        "tema": {
            "ca": "PAU — filtre de correu brossa",
            "en": "PAU — spam filter",
        },
        "enunciat": {
            "ca": (
                "Un usuari d'Internet ha estimat que el 25 % dels correus "
                "electrònics que rep són correu brossa, mentre que la resta no "
                "ho són. Per a facilitar la classificació del correu, s'ha "
                "instal·lat un filtre que envia a la carpeta de correu brossa el "
                "95 % dels missatges que efectivament ho són. Malauradament, "
                "aquest filtre deixa a la safata d'entrada només el 90 % dels "
                "missatges bons (i la resta els envia a la carpeta de correu "
                "brossa).\n\n"
                "**a)** Quina és la probabilitat que un missatge sigui enviat pel "
                "filtre a la carpeta de correu brossa? *[0,75 punts]*\n\n"
                "**b)** Un dia, aquest usuari obre la carpeta de correu brossa. "
                "Quin percentatge de missatges que no són correu brossa hi trobarà? "
                "*[0,75 punts]*"
            ),
            "en": (
                "An Internet user has estimated that 25% of the emails they receive "
                "are spam, the rest are not. To help with classification, a filter "
                "sends 95% of the actual spam messages to the spam folder. "
                "Unfortunately, the filter keeps only 90% of legitimate messages in "
                "the inbox (sending the rest to the spam folder).\n\n"
                "**a)** What is the probability that a message is sent by the filter "
                "to the spam folder? *[0.75 pts]*\n\n"
                "**b)** One day, the user opens the spam folder. What percentage of "
                "messages there are NOT actually spam? *[0.75 pts]*"
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
            "COND_invertit",
            "TOT_branca_oblidada",
            "TOT_suma_vs_producte",
            "BAY_invertit",
            "BAY_no_prob_total",
        ],
        "passos": [
            # --------------------------------------------------------
            # Pas 1 — identificació de les dades + complementari
            # --------------------------------------------------------
            # La trampa principal: l'enunciat dóna P(safata | B̄) = 0.90,
            # NO P(CB | B̄). L'alumne ha de fer P(CB | B̄) = 1 − 0.90 = 0.10.
            # Si oblida aquest pas, el càlcul de P(CB) li donarà 0.95·0.25
            # + 0.90·0.75 = 0.9125 (totalment fora de rang).
            {
                "id": 1,
                "text": {
                    "ca": (
                        "**Apartat a)** Defineix els successos rellevants i identifica "
                        "les quatre probabilitats que dóna l'enunciat. Suggeriment: "
                        "usa B = «rebre correu brossa», CB = «el filtre envia el "
                        "missatge a la carpeta de correu brossa». Atenció: pensa bé "
                        "què val P(CB|B̄) a partir del 90 % de l'enunciat."
                    ),
                    "en": (
                        "**Part a)** Define the relevant events and identify the four "
                        "probabilities given by the problem. Hint: use B = \"receive "
                        "spam\", CB = \"filter sends the message to the spam folder\". "
                        "Beware: think carefully what P(CB|B̄) is given the 90 % in "
                        "the statement."
                    ),
                },
                "expected_summary": (
                    "P(B) = 0.25, P(B̄) = 0.75, P(CB|B) = 0.95, P(CB|B̄) = 1 − 0.90 = 0.10. "
                    "Atenció: l'enunciat dóna que el 90 % dels missatges bons queden a "
                    "la safata d'entrada, és a dir P(safata|B̄) = 0.90; per tant "
                    "P(CB|B̄) = 0.10, no 0.90."
                ),
                "key_concepts": ["def_prob_condicionada"],
                "input_type": "free_text",
                "expected_value": None,
                "typical_error": (
                    "taking P(CB|B̄) = 0.90 directly (without complementing), or "
                    "inverting P(CB|B) with P(B|CB)"
                ),
                "typical_error_label": "COND_invertit",
            },
            # --------------------------------------------------------
            # Pas 2 — probabilitat total per a P(CB)
            # --------------------------------------------------------
            # 0.3125 surt exacte; tolerància 1e-4 no és problema.
            {
                "id": 2,
                "text": {
                    "ca": (
                        "**Apartat a)** Aplica la fórmula de la probabilitat total "
                        "per calcular P(CB), la probabilitat que un missatge sigui "
                        "enviat a la carpeta de correu brossa. Dóna el resultat com "
                        "a decimal (4 xifres després de la coma)."
                    ),
                    "en": (
                        "**Part a)** Apply the total probability theorem to compute "
                        "P(CB), the probability that a message is sent to the spam "
                        "folder. Give the answer as a decimal (4 decimal places)."
                    ),
                },
                "expected_summary": (
                    "P(CB) = P(CB|B)·P(B) + P(CB|B̄)·P(B̄) = 0.95·0.25 + 0.10·0.75 "
                    "= 0.2375 + 0.075 = 0.3125"
                ),
                "key_concepts": ["def_prob_total"],
                "input_type": "decimal",
                "expected_value": 0.3125,
                "typical_error": (
                    "using 0.90 instead of 0.10 for P(CB|B̄) (forgetting to complement), "
                    "giving 0.95·0.25 + 0.90·0.75 = 0.9125"
                ),
                "typical_error_label": "TOT_branca_oblidada",
            },
            # --------------------------------------------------------
            # Pas 3 — Bayes per a P(B̄|CB)
            # --------------------------------------------------------
            # Fraction "6/25" o decimal "0.24". Càlcul:
            #   (0.10·0.75) / 0.3125 = 0.075/0.3125 = 6/25 = 0.24
            {
                "id": 3,
                "text": {
                    "ca": (
                        "**Apartat b)** Calcula P(B̄|CB), la probabilitat que un "
                        "missatge a la carpeta de correu brossa no sigui realment "
                        "brossa. Aplica la fórmula de Bayes. Dóna el resultat com "
                        "a fracció reduïda o decimal amb 4 xifres."
                    ),
                    "en": (
                        "**Part b)** Compute P(B̄|CB), the probability that a "
                        "message in the spam folder is NOT actually spam. Apply "
                        "Bayes' formula. Give the answer as a reduced fraction or "
                        "4-digit decimal."
                    ),
                },
                "expected_summary": (
                    "P(B̄|CB) = P(CB|B̄)·P(B̄) / P(CB) = (0.10·0.75) / 0.3125 "
                    "= 0.075 / 0.3125 = 6/25 = 0.24. És a dir, un 24 % dels "
                    "missatges a la carpeta de brossa no són realment brossa."
                ),
                "key_concepts": ["def_bayes"],
                "input_type": "fraction",
                "expected_value": "6/25",
                "typical_error": (
                    "inverting the Bayes formula (using P(CB|B) in the numerator "
                    "instead of P(CB|B̄)), or using P(CB|B̄)·P(B) in the numerator"
                ),
                "typical_error_label": "BAY_invertit",
            },
        ],
    },

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

    # ============================================================
    # PROB-PAU-04 — BAYES FANS (PAU 2025-26, problema 4)
    # ============================================================
    # Origen: enunciat literal del recull de problemes PAU del
    # Departament de Matemàtiques (curs 2025-26).
    #
    # 9 boles marcades amb les lletres B-A-Y-E-S-F-A-N-S. Inventari:
    # - dues A
    # - dues S
    # - cinc lletres úniques: B, Y, E, F, N
    #
    # Estructura: dos apartats amb 2 sub-preguntes cadascun (4 passos
    # en total).
    #   a) Sense reemplaçament:
    #      (i) P(primera bola = A o E) per Laplace simple.
    #      (ii) P(les dues boles diferents) via complementari.
    #   b) Amb reemplaçament, 5 extraccions (X ~ Bin(5, 2/9)):
    #      (i) P(cap A) = (7/9)⁵ ≈ 0.2846.
    #      (ii) P(almenys 2 A) = 1 − P(X=0) − P(X=1) ≈ 0.3088, via
    #           complementari (la suma directa P(X=2)+...+P(X=5) és
    #           molt més laboriosa).
    #
    # Aquest problema és el primer en què apareix `def_succes_complementari`
    # com a dependència principal (a 2 dels 4 passos).
    #
    # Node: PROB-L4-BIN (el binomial és la peça més profunda).
    "PROB-PAU-04": {
        "id": "PROB-PAU-04",
        "node": "PROB-L4-BIN",
        "familia": "PROB-PAU",
        "nivell": 3,
        "tema": {
            "ca": "PAU — boles BAYES FANS (Laplace + binomial + complementari)",
            "en": "PAU — BAYES FANS balls (Laplace + binomial + complement)",
        },
        "enunciat": {
            "ca": (
                "L'Andreu posa dins d'una bossa nou boles marcades amb les "
                "lletres **B, A, Y, E, S, F, A, N, S** (atenció: hi ha dues A "
                "i dues S; les altres cinc lletres són úniques).\n\n"
                "**a)** Treu de la bossa dues boles a l'atzar, una darrere "
                "l'altra i **sense reemplaçament** (no retorna la primera bola "
                "abans de treure la segona).\n"
                "  — *(i)* Calcula la probabilitat que la primera bola sigui "
                "una A o una E. *[0,5 punts]*\n"
                "  — *(ii)* Calcula la probabilitat que les dues boles siguin "
                "diferents. *[0,75 punts]*\n\n"
                "**b)** L'Andreu torna a posar totes les boles a la bossa i en "
                "treu cinc a l'atzar, una darrere l'altra però ara **amb "
                "reemplaçament** (cada bola torna a la bossa abans d'agafar la "
                "següent).\n"
                "  — *(i)* Calcula la probabilitat que no hagi tret cap A. "
                "*[0,5 punts]*\n"
                "  — *(ii)* Calcula la probabilitat que hagi tret almenys dues A. "
                "*[0,75 punts]*"
            ),
            "en": (
                "Andreu places nine balls in a bag marked with the letters "
                "**B, A, Y, E, S, F, A, N, S** (note: there are two A's and two "
                "S's; the other five letters are unique).\n\n"
                "**a)** He draws two balls at random, one after the other and "
                "**without replacement**.\n"
                "  — *(i)* What is the probability that the first ball is an A "
                "or an E? *[0.5 pts]*\n"
                "  — *(ii)* What is the probability that the two balls are "
                "different? *[0.75 pts]*\n\n"
                "**b)** Andreu puts all the balls back and draws five at "
                "random, one after the other, now **with replacement**.\n"
                "  — *(i)* What is the probability that no A was drawn? *[0.5 pts]*\n"
                "  — *(ii)* What is the probability of at least two A's? *[0.75 pts]*"
            ),
        },
        "input_mode": "free_text",
        "answer_language": "ca",
        "dependencies": [
            "def_espai_mostral",
            "def_laplace",
            "def_succes_complementari",
            "def_binomial",
            "def_independencia",
        ],
        "errors_freqüents": [
            "LAP_favorable_total_swap",
            "LAP_doble_recompte",
            "COMP_no_complement",
            "BIN_n_k_invertits",
            "BIN_p_vs_q",
            "BIN_complementari_oblidat",
        ],
        "passos": [
            # --------------------------------------------------------
            # Pas 1 — apartat (a-i): Laplace simple
            # --------------------------------------------------------
            # Inventari: 2 A + 1 E = 3 casos favorables sobre 9.
            # Trampa: confondre amb 3/8 (oblidar que la primera extracció
            # encara té les 9 boles).
            {
                "id": 1,
                "text": {
                    "ca": (
                        "**Apartat a-i)** Calcula la probabilitat que la primera "
                        "bola extreta sigui una A o una E. Dóna el resultat com a "
                        "fracció reduïda o decimal amb 4 xifres."
                    ),
                    "en": (
                        "**Part a-i)** Compute the probability that the first ball "
                        "drawn is an A or an E. Give the answer as a reduced fraction "
                        "or 4-digit decimal."
                    ),
                },
                "expected_summary": (
                    "Inventari: 2 A + 1 E = 3 boles favorables sobre 9 totals. "
                    "P(A o E a la 1a extracció) = 3/9 = 1/3 ≈ 0.3333."
                ),
                "key_concepts": ["def_laplace", "def_espai_mostral"],
                "input_type": "fraction",
                "expected_value": "1/3",
                "typical_error": (
                    "writing 3/8 (treating it as if a ball had already been "
                    "drawn), or 2/9 (counting only the A's and missing the E)"
                ),
                "typical_error_label": "LAP_favorable_total_swap",
            },
            # --------------------------------------------------------
            # Pas 2 — apartat (a-ii): complementari sobre P(iguals)
            # --------------------------------------------------------
            # P(iguals) = P(AA) + P(SS) = 2·(2/9)·(1/8) = 4/72 = 1/18.
            # P(diferents) = 17/18 ≈ 0.9444.
            # Trampa: intentar enumerar tots els parells diferents
            # directament és gairebé impossible; cal complementari.
            {
                "id": 2,
                "text": {
                    "ca": (
                        "**Apartat a-ii)** Calcula la probabilitat que les dues "
                        "boles extretes (sense reemplaçament) siguin diferents. "
                        "Suggeriment: pensa per quins casos NO són diferents i "
                        "passa al complementari. Dóna el resultat com a fracció "
                        "reduïda o decimal amb 4 xifres."
                    ),
                    "en": (
                        "**Part a-ii)** Compute the probability that the two balls "
                        "drawn (without replacement) are different. Hint: think "
                        "which cases are NOT different and pass to the complement. "
                        "Give the answer as a reduced fraction or 4-digit decimal."
                    ),
                },
                "expected_summary": (
                    "Les dues boles són iguals només si són dues A o dues S. "
                    "P(dues A) = (2/9)·(1/8) = 1/36; P(dues S) = (2/9)·(1/8) = 1/36. "
                    "P(iguals) = 1/36 + 1/36 = 1/18. "
                    "P(diferents) = 1 − P(iguals) = 1 − 1/18 = 17/18 ≈ 0.9444."
                ),
                "key_concepts": ["def_succes_complementari", "def_prob_condicionada"],
                "input_type": "fraction",
                "expected_value": "17/18",
                "typical_error": (
                    "trying to enumerate all different pairs directly (very "
                    "error-prone), or forgetting that there are TWO kinds of "
                    "'equal' pairs (AA and SS) and computing only 1/36"
                ),
                "typical_error_label": "COMP_no_complement",
            },
            # --------------------------------------------------------
            # Pas 3 — apartat (b-i): P(cap A) en 5 extraccions amb reempl.
            # --------------------------------------------------------
            # X ~ Bin(5, 2/9). P(X=0) = (7/9)⁵ = 16807/59049 ≈ 0.2846.
            # Tolerància 1e-4: l'alumne ha de donar 4 decimals; 0.284
            # quedaria fora i el judge ho ha de capturar.
            {
                "id": 3,
                "text": {
                    "ca": (
                        "**Apartat b-i)** Amb reemplaçament i 5 extraccions, "
                        "calcula la probabilitat que no s'hagi tret cap A. "
                        "Suggeriment: modela el nombre d'A extretes amb una "
                        "distribució binomial. Dóna el resultat com a decimal "
                        "amb 4 xifres."
                    ),
                    "en": (
                        "**Part b-i)** With replacement and 5 draws, compute the "
                        "probability that no A was drawn. Hint: model the number "
                        "of A's drawn as a binomial distribution. Give the result "
                        "as a 4-digit decimal."
                    ),
                },
                "expected_summary": (
                    "Sigui X = nombre d'A extretes en 5 extraccions amb reemplaçament. "
                    "X ~ B(n=5, p=2/9). P(cap A) = P(X=0) = C(5,0)·(2/9)⁰·(7/9)⁵ "
                    "= (7/9)⁵ = 16807/59049 ≈ 0.2846."
                ),
                "key_concepts": ["def_binomial", "def_independencia"],
                "input_type": "decimal",
                "expected_value": 0.2846,
                "typical_error": (
                    "using p=7/9 instead of p=2/9 in the binomial formula, or "
                    "computing P(X=0) = (2/9)⁵ ≈ 0.00057 (using p instead of 1−p)"
                ),
                "typical_error_label": "BIN_p_vs_q",
            },
            # --------------------------------------------------------
            # Pas 4 — apartat (b-ii): P(almenys 2 A) via complementari
            # --------------------------------------------------------
            # P(X≥2) = 1 − P(X=0) − P(X=1) = 1 − 16807/59049 − 24010/59049
            # = 18232/59049 ≈ 0.3088.
            # Reusem P(X=0) del pas 3.
            {
                "id": 4,
                "text": {
                    "ca": (
                        "**Apartat b-ii)** Amb reemplaçament i 5 extraccions, "
                        "calcula la probabilitat que hagi tret almenys dues A. "
                        "Suggeriment: pensa via complementari, "
                        "P(X≥2) = 1 − P(X=0) − P(X=1). Dóna el resultat com a "
                        "decimal amb 4 xifres."
                    ),
                    "en": (
                        "**Part b-ii)** With replacement and 5 draws, compute the "
                        "probability of at least two A's. Hint: use the complement, "
                        "P(X≥2) = 1 − P(X=0) − P(X=1). Give the result as a 4-digit "
                        "decimal."
                    ),
                },
                "expected_summary": (
                    "X ~ B(5, 2/9). P(X=1) = C(5,1)·(2/9)¹·(7/9)⁴ "
                    "= 5·(2/9)·(2401/6561) = 24010/59049 ≈ 0.4066. "
                    "P(X≥2) = 1 − P(X=0) − P(X=1) = 1 − 16807/59049 − 24010/59049 "
                    "= 18232/59049 ≈ 0.3088."
                ),
                "key_concepts": ["def_binomial", "def_succes_complementari"],
                "input_type": "decimal",
                "expected_value": 0.3088,
                "typical_error": (
                    "trying to sum P(X=2)+P(X=3)+P(X=4)+P(X=5) directly (more "
                    "error-prone), or forgetting to subtract one of the two terms "
                    "(only subtracting P(X=0))"
                ),
                "typical_error_label": "COMP_no_complement",
            },
        ],
    },

    # ============================================================
    # PROB-PAU-05 — Rut deures (PAU 2025-26, problema 5)
    # ============================================================
    # Origen: enunciat literal del recull de problemes PAU del
    # Departament de Matemàtiques (curs 2025-26).
    #
    # Tres apartats:
    #   a) Probabilitat total per a P(C).
    #   b) Bayes per a P(R|C) (reusa el resultat de a).
    #   c) Binomial: en 5 problemes, P(almenys 4 correctes).
    #
    # SUBTILESA TÈCNICA al pas (c): P(C) = 19/30 = 0.6333… (decimal
    # no exacte). Si el pas 2 captura 19/30 com a fracció, el càlcul
    # binomial del pas 4 a partir de p=19/30 dóna 0.3969; amb p=0.633
    # arrodonit, l'alumne arriba a 0.3962. Aquests dos valors NO entren
    # dins una tolerància 1e-4, així que el pas 4 és `free_text` per
    # evitar penalitzar arrodoniments raonables. La IA jutja la
    # plausibilitat (esperar ~0.39-0.40).
    #
    # Decomposició en 4 passos:
    #   pas 1: identificar dades (free_text)
    #   pas 2: P(C) = 19/30 ≈ 0.6333 (fraction, accepta forma decimal)
    #   pas 3: P(R|C) = 15/19 ≈ 0.7895 (fraction)
    #   pas 4: P(X≥4 | n=5, p≈0.633) ≈ 0.396 (free_text — vegeu nota)
    #
    # Node: PROB-L4-BIN (el binomial al pas final).
    "PROB-PAU-05": {
        "id": "PROB-PAU-05",
        "node": "PROB-L4-BIN",
        "familia": "PROB-PAU",
        "nivell": 3,
        "tema": {
            "ca": "PAU — la Rut i els deures de matemàtiques",
            "en": "PAU — Rut and her maths homework",
        },
        "enunciat": {
            "ca": (
                "La Rut fa servir el mètode següent per a fer els problemes de "
                "matemàtiques: tira un dau equilibrat i, si el resultat és com a "
                "màxim 4, pensa i resol el problema ella mateixa; si el resultat "
                "és 5 o 6, busca la solució del problema per Internet i la copia. "
                "Quan és ella qui ha pensat la solució, la resposta és correcta "
                "en el 75 % dels casos; quan copia la solució d'Internet, la "
                "resposta és correcta només en el 40 % dels casos.\n\n"
                "**a)** Quina és la probabilitat que la solució d'un problema "
                "respost seguint aquest mètode sigui correcta? *[0,75 punts]*\n\n"
                "**b)** Quina és la probabilitat que un problema l'hagi resolt la "
                "Rut si sabem que la solució és correcta? *[0,75 punts]*\n\n"
                "**c)** Demà la Rut ha d'entregar 5 problemes de matemàtiques. "
                "Quina és la probabilitat que n'hi hagi almenys 4 de correctes? "
                "*[1 punt]*"
            ),
            "en": (
                "Rut uses the following method for her maths problems: she rolls "
                "a fair die and, if the result is at most 4, she solves the "
                "problem herself; if the result is 5 or 6, she copies the "
                "solution from the Internet. When she thinks the solution "
                "herself, it is correct 75% of the time; when she copies from "
                "the Internet, it is correct only 40% of the time.\n\n"
                "**a)** What is the probability that a problem solved with this "
                "method has the correct solution? *[0.75 pts]*\n\n"
                "**b)** What is the probability that Rut solved a problem "
                "herself, given that the solution is correct? *[0.75 pts]*\n\n"
                "**c)** Tomorrow Rut has to submit 5 maths problems. What is the "
                "probability that at least 4 of them are correct? *[1 pt]*"
            ),
        },
        "input_mode": "free_text",
        "answer_language": "ca",
        "dependencies": [
            "def_prob_condicionada",
            "def_prob_total",
            "def_bayes",
            "def_binomial",
            "def_succes_complementari",
        ],
        "errors_freqüents": [
            "COND_invertit",
            "TOT_branca_oblidada",
            "BAY_invertit",
            "BAY_no_prob_total",
            "BIN_complementari_oblidat",
        ],
        "passos": [
            # --------------------------------------------------------
            # Pas 1 — identificació de les dades
            # --------------------------------------------------------
            # Trampa subtil: el dau té 6 cares; ≤4 → P(R)=4/6=2/3,
            # 5 o 6 → P(I)=2/6=1/3. Alguns alumnes inverteixen els
            # rangs ("com a màxim 4" inclou el 4 o no?).
            {
                "id": 1,
                "text": {
                    "ca": (
                        "Defineix els successos rellevants i identifica les quatre "
                        "probabilitats que dóna l'enunciat. Suggeriment: usa "
                        "R = «la Rut resol el problema ella mateixa», "
                        "I = «copia la solució d'Internet», "
                        "C = «la solució és correcta»."
                    ),
                    "en": (
                        "Define the relevant events and identify the four "
                        "probabilities given by the problem. Hint: use "
                        "R = \"Rut solves the problem herself\", "
                        "I = \"she copies from the Internet\", "
                        "C = \"the solution is correct\"."
                    ),
                },
                "expected_summary": (
                    "Dau equilibrat: «com a màxim 4» = {1,2,3,4} → P(R) = 4/6 = 2/3. "
                    "«5 o 6» → P(I) = 2/6 = 1/3. P(C|R) = 0.75, P(C|I) = 0.40. "
                    "Atenció: l'alumne ha de notar que R i I formen una partició de "
                    "l'espai mostral (és sempre R o I, mai cap d'altre)."
                ),
                "key_concepts": ["def_prob_condicionada"],
                "input_type": "free_text",
                "expected_value": None,
                "typical_error": (
                    "computing P(R)=1/2 (treating the die as 'half ≤4, half >4') "
                    "or inverting P(C|R) with P(R|C)"
                ),
                "typical_error_label": "COND_invertit",
            },
            # --------------------------------------------------------
            # Pas 2 — probabilitat total per a P(C)
            # --------------------------------------------------------
            # 0.75·(2/3) + 0.4·(1/3) = 1/2 + 2/15 = 19/30 = 0.6333…
            # Acceptem fracció "19/30" o decimal "0.6333".
            # Atenció: el verificador té tolerància 1e-4. "0.633" (3
            # decimals) queda fora i seria typical_error.
            {
                "id": 2,
                "text": {
                    "ca": (
                        "**Apartat a)** Aplica la fórmula de la probabilitat total "
                        "per calcular P(C), la probabilitat que un problema fet amb "
                        "aquest mètode sigui correcte. Dóna el resultat com a "
                        "fracció reduïda o decimal amb 4 xifres."
                    ),
                    "en": (
                        "**Part a)** Apply the total probability theorem to compute "
                        "P(C), the probability that a problem solved with this "
                        "method is correct. Give the answer as a reduced fraction or "
                        "4-digit decimal."
                    ),
                },
                "expected_summary": (
                    "P(C) = P(C|R)·P(R) + P(C|I)·P(I) = 0.75·(2/3) + 0.40·(1/3) "
                    "= 0.5 + 0.4/3 = 1/2 + 2/15 = 15/30 + 4/30 = 19/30 ≈ 0.6333."
                ),
                "key_concepts": ["def_prob_total"],
                "input_type": "fraction",
                "expected_value": "19/30",
                "typical_error": (
                    "computing only one branch (0.5 = 0.75·2/3) and forgetting the "
                    "Internet branch, or summing along a branch instead of multiplying"
                ),
                "typical_error_label": "TOT_branca_oblidada",
            },
            # --------------------------------------------------------
            # Pas 3 — Bayes per a P(R|C)
            # --------------------------------------------------------
            # P(R|C) = P(C|R)·P(R)/P(C) = (0.75·2/3)/(19/30) = (1/2)/(19/30)
            # = (1/2)·(30/19) = 30/38 = 15/19 ≈ 0.7895.
            {
                "id": 3,
                "text": {
                    "ca": (
                        "**Apartat b)** Aplica la fórmula de Bayes per calcular "
                        "P(R|C), la probabilitat que la Rut hagi resolt el problema "
                        "ella mateixa donat que la solució és correcta. Dóna el "
                        "resultat com a fracció reduïda o decimal amb 4 xifres."
                    ),
                    "en": (
                        "**Part b)** Apply Bayes' formula to compute P(R|C), the "
                        "probability that Rut solved the problem herself given that "
                        "the solution is correct. Give the answer as a reduced "
                        "fraction or 4-digit decimal."
                    ),
                },
                "expected_summary": (
                    "P(R|C) = P(C|R)·P(R) / P(C) = (0.75·2/3) / (19/30) "
                    "= (1/2) · (30/19) = 30/38 = 15/19 ≈ 0.7895."
                ),
                "key_concepts": ["def_bayes"],
                "input_type": "fraction",
                "expected_value": "15/19",
                "typical_error": (
                    "inverting the Bayes formula (P(C|R)·P(C) instead of P(C|R)·P(R), "
                    "or dividing by P(R) instead of P(C))"
                ),
                "typical_error_label": "BAY_invertit",
            },
            # --------------------------------------------------------
            # Pas 4 — binomial amb p = 19/30 i complementari
            # --------------------------------------------------------
            # free_text per la raó tècnica explicada al capçal del problema:
            # arrodonir p=0.633 vs p=19/30 dóna respostes que difereixen
            # en >1e-4, així que no podem fixar un `expected_value` decimal.
            # La IA jutja l'ordre de magnitud (~0.396-0.397).
            {
                "id": 4,
                "text": {
                    "ca": (
                        "**Apartat c)** En 5 problemes resolts pel mètode de la "
                        "Rut, calcula la probabilitat que n'hi hagi almenys 4 de "
                        "correctes. Modela el nombre de problemes correctes amb "
                        "una distribució binomial usant la P(C) del pas anterior. "
                        "Indica clarament la fórmula que apliques i el resultat "
                        "final amb 3 o 4 xifres decimals."
                    ),
                    "en": (
                        "**Part c)** Out of 5 problems solved with Rut's method, "
                        "compute the probability that at least 4 are correct. "
                        "Model the number of correct problems with a binomial "
                        "distribution using P(C) from the previous step. State "
                        "the formula clearly and give the final result with 3 or "
                        "4 decimal digits."
                    ),
                },
                "expected_summary": (
                    "Sigui X = nombre de problemes correctes en 5 problemes. "
                    "X ~ B(n=5, p=P(C)≈0.6333). "
                    "P(X≥4) = P(X=4) + P(X=5) = C(5,4)·p⁴·(1−p) + p⁵ "
                    "= 5·0.6333⁴·0.3667 + 0.6333⁵ ≈ 0.2947 + 0.1017 ≈ 0.396. "
                    "(Usant p = 19/30 exacte la resposta surt ≈ 0.3969; usant "
                    "p ≈ 0.633 arrodonit, ≈ 0.3962. Tots dos són acceptables.)"
                ),
                "key_concepts": ["def_binomial", "def_succes_complementari"],
                "input_type": "free_text",
                "expected_value": None,
                "typical_error": (
                    "computing only P(X=4) and forgetting P(X=5) (or vice versa), "
                    "i.e. answering 'at least 4' with only one term"
                ),
                "typical_error_label": "BIN_complementari_oblidat",
            },
        ],
    },

    # ============================================================
    # PROB-PAU-06 — monitor Holter d'arrítmies (PAU 2025-26, problema 6)
    # ============================================================
    # Origen: enunciat literal del recull de problemes PAU del
    # Departament de Matemàtiques (curs 2025-26).
    #
    # NOTA SOBRE L'ENUNCIAT: la pàgina 7 del recull mostra les
    # solucions completes (a, b, c) però el bloc d'enunciat publicat
    # només llista (a) i (b). L'apartat (c) — Bayes amb H̄ — es
    # dedueix sense ambigüitat de la solució (a-c). Hi afegim el
    # text de l'apartat (c) reconstruït a partir de la solució per
    # tenir un problema complet i avaluable.
    #
    # Tres apartats:
    #   a) Binomial + complementari: 4 persones, P(≥1 amb arrítmia).
    #   b) Probabilitat total: P(H) per a una persona a l'atzar.
    #   c) Bayes: donat diagnòstic negatiu (H̄), P(pateixi arrítmia)?
    #
    # Decomposició en 4 passos:
    #   pas 1: apartat (a), binomial+complement, decimal
    #   pas 2: definir els esdeveniments H, A i identificar les
    #          quatre probabilitats per a (b) i (c) (free_text)
    #   pas 3: apartat (b), probabilitat total P(H), decimal
    #   pas 4: apartat (c), Bayes P(A|H̄), decimal
    #
    # Node: PROB-L4-BIN (binomial al pas 1; el problema completa
    # tot el camí del DAG, però el binomial és el punt més profund
    # activat des del primer pas).
    "PROB-PAU-06": {
        "id": "PROB-PAU-06",
        "node": "PROB-L4-BIN",
        "familia": "PROB-PAU",
        "nivell": 3,
        "tema": {
            "ca": "PAU — monitor Holter i diagnòstic d'arrítmies",
            "en": "PAU — Holter monitor and arrhythmia diagnosis",
        },
        "enunciat": {
            "ca": (
                "S'estima que el 20 % dels habitants d'una regió pateix algun "
                "tipus d'arrítmia. Per a diagnosticar-la, hi ha la possibilitat "
                "de col·locar al pacient un monitor Holter, que detecta "
                "l'arrítmia en un 95 % dels casos de persones que la pateixen, "
                "però que també dóna falsos positius, per motius elèctrics, en "
                "persones que no pateixen arrítmies en un 0,5 % dels casos.\n\n"
                "**a)** Si escollim 4 persones a l'atzar, quina és la probabilitat "
                "que almenys una d'elles pateixi arrítmies? *[0,75 punts]*\n\n"
                "**b)** Quina és la probabilitat que una persona escollida a "
                "l'atzar obtingui un diagnòstic positiu d'arrítmia? *[0,75 punts]*\n\n"
                "**c)** Si una persona escollida a l'atzar obté un diagnòstic "
                "negatiu al monitor Holter, quina és la probabilitat que "
                "realment pateixi arrítmia? *[1 punt]*"
            ),
            "en": (
                "It is estimated that 20% of the inhabitants of a region suffer "
                "from some type of arrhythmia. To diagnose it, one option is to "
                "place a Holter monitor on the patient, which detects arrhythmia "
                "in 95% of cases of people who suffer from it, but which also "
                "gives false positives, for electrical reasons, in 0.5% of "
                "people without arrhythmias.\n\n"
                "**a)** If we choose 4 random people, what is the probability "
                "that at least one of them suffers from arrhythmia? *[0.75 pts]*\n\n"
                "**b)** What is the probability that a random person gets a "
                "positive arrhythmia diagnosis? *[0.75 pts]*\n\n"
                "**c)** If a random person gets a NEGATIVE diagnosis from the "
                "Holter monitor, what is the probability that they really "
                "suffer arrhythmia? *[1 pt]*"
            ),
        },
        "input_mode": "free_text",
        "answer_language": "ca",
        "dependencies": [
            "def_prob_condicionada",
            "def_prob_total",
            "def_bayes",
            "def_binomial",
            "def_succes_complementari",
        ],
        "errors_freqüents": [
            "COND_invertit",
            "TOT_branca_oblidada",
            "BAY_invertit",
            "BAY_no_prob_total",
            "COMP_no_complement",
        ],
        "passos": [
            # --------------------------------------------------------
            # Pas 1 — apartat (a): binomial amb complementari
            # --------------------------------------------------------
            # N ~ Bin(4, 0.2). P(N≥1) = 1 − P(N=0) = 1 − 0.8⁴ = 0.5904.
            # Trampa típica: sumar P(N=1)+P(N=2)+P(N=3)+P(N=4) — molt
            # més laboriós i propens a errors.
            {
                "id": 1,
                "text": {
                    "ca": (
                        "**Apartat a)** Si escollim 4 persones a l'atzar, calcula "
                        "la probabilitat que almenys una pateixi arrítmies. "
                        "Suggeriment: pensa via complementari, "
                        "P(N≥1) = 1 − P(N=0). Dóna el resultat com a decimal "
                        "amb 4 xifres."
                    ),
                    "en": (
                        "**Part a)** If we choose 4 random people, compute the "
                        "probability that at least one suffers arrhythmia. "
                        "Hint: use the complement, P(N≥1) = 1 − P(N=0). Give the "
                        "result as a 4-digit decimal."
                    ),
                },
                "expected_summary": (
                    "Sigui A = «pateix arrítmia», P(A) = 0.20. Sigui N = nombre "
                    "de persones amb arrítmia en una mostra de 4. "
                    "N ~ B(n=4, p=0.20). "
                    "P(N≥1) = 1 − P(N=0) = 1 − (1−0.20)⁴ = 1 − 0.8⁴ "
                    "= 1 − 0.4096 = 0.5904."
                ),
                "key_concepts": ["def_binomial", "def_succes_complementari"],
                "input_type": "decimal",
                "expected_value": 0.5904,
                "typical_error": (
                    "summing P(N=1)+P(N=2)+P(N=3)+P(N=4) directly instead of "
                    "using the complement (much more error-prone), or computing "
                    "P(N=0) = 0.2⁴ = 0.0016 (swapping p and 1−p)"
                ),
                "typical_error_label": "COMP_no_complement",
            },
            # --------------------------------------------------------
            # Pas 2 — identificació dels esdeveniments per a (b) i (c)
            # --------------------------------------------------------
            # P(A) = 0.20, P(Ā) = 0.80, P(H|A) = 0.95, P(H|Ā) = 0.005.
            # ATENCIÓ: 0.5 % = 0.005, NO 0.05. És un error comú al
            # passar de tant per cent a decimal.
            {
                "id": 2,
                "text": {
                    "ca": (
                        "Per als apartats (b) i (c), defineix l'esdeveniment "
                        "H = «el monitor Holter dóna diagnòstic positiu» i "
                        "identifica les quatre probabilitats que necessitarem. "
                        "Suggeriment: vigila amb el 0,5 % en passar-lo a decimal."
                    ),
                    "en": (
                        "For parts (b) and (c), define the event H = \"the Holter "
                        "monitor gives a positive diagnosis\" and identify the "
                        "four probabilities we'll need. Hint: be careful with the "
                        "0.5% when converting to decimal."
                    ),
                },
                "expected_summary": (
                    "Esdeveniments: A = «pateix arrítmia», H = «diagnòstic positiu "
                    "del Holter». De l'enunciat: P(A) = 0.20, P(Ā) = 0.80, "
                    "P(H|A) = 0.95, P(H|Ā) = 0.005. Atenció: 0.5 % = 0.005, NO 0.05."
                ),
                "key_concepts": ["def_prob_condicionada"],
                "input_type": "free_text",
                "expected_value": None,
                "typical_error": (
                    "using P(H|Ā) = 0.05 (misreading 0.5% as 5%), or inverting "
                    "P(H|A) with P(A|H)"
                ),
                "typical_error_label": "COND_invertit",
            },
            # --------------------------------------------------------
            # Pas 3 — apartat (b): probabilitat total per a P(H)
            # --------------------------------------------------------
            # P(H) = 0.95·0.20 + 0.005·0.80 = 0.190 + 0.004 = 0.194.
            {
                "id": 3,
                "text": {
                    "ca": (
                        "**Apartat b)** Aplica la fórmula de la probabilitat total "
                        "per calcular P(H), la probabilitat que una persona a "
                        "l'atzar obtingui un diagnòstic positiu al Holter. Dóna "
                        "el resultat com a decimal amb 4 xifres."
                    ),
                    "en": (
                        "**Part b)** Apply the total probability theorem to compute "
                        "P(H), the probability that a random person gets a positive "
                        "diagnosis at the Holter. Give the result as a 4-digit "
                        "decimal."
                    ),
                },
                "expected_summary": (
                    "P(H) = P(H|A)·P(A) + P(H|Ā)·P(Ā) = 0.95·0.20 + 0.005·0.80 "
                    "= 0.190 + 0.004 = 0.194."
                ),
                "key_concepts": ["def_prob_total"],
                "input_type": "decimal",
                "expected_value": 0.194,
                "typical_error": (
                    "computing 0.95·0.20 + 0.05·0.80 = 0.230 (using 0.05 instead "
                    "of 0.005 for P(H|Ā)), or omitting the second branch"
                ),
                "typical_error_label": "TOT_branca_oblidada",
            },
            # --------------------------------------------------------
            # Pas 4 — apartat (c): Bayes per a P(A|H̄)
            # --------------------------------------------------------
            # P(A|H̄) = P(H̄|A)·P(A)/P(H̄) = (1-0.95)·0.20/(1-0.194)
            #        = 0.05·0.20/0.806 = 0.01/0.806 ≈ 0.0124.
            # Tolerància 1e-4 ⇒ acceptem 0.0123–0.0125.
            {
                "id": 4,
                "text": {
                    "ca": (
                        "**Apartat c)** Aplica la fórmula de Bayes per calcular "
                        "P(A|H̄), la probabilitat que una persona realment pateixi "
                        "arrítmia donat que ha obtingut un diagnòstic NEGATIU al "
                        "Holter. Dóna el resultat com a decimal amb 4 xifres."
                    ),
                    "en": (
                        "**Part c)** Apply Bayes' formula to compute P(A|H̄), the "
                        "probability that a person really suffers arrhythmia given "
                        "they got a NEGATIVE diagnosis at the Holter. Give the "
                        "result as a 4-digit decimal."
                    ),
                },
                "expected_summary": (
                    "P(A|H̄) = P(H̄|A)·P(A) / P(H̄) = (1 − P(H|A))·P(A) / (1 − P(H)) "
                    "= (1 − 0.95)·0.20 / (1 − 0.194) = 0.05·0.20 / 0.806 "
                    "= 0.01 / 0.806 ≈ 0.0124. És a dir, un diagnòstic negatiu "
                    "redueix moltíssim la probabilitat (de 20 % a ~1.24 %)."
                ),
                "key_concepts": ["def_bayes", "def_succes_complementari"],
                "input_type": "decimal",
                "expected_value": 0.0124,
                "typical_error": (
                    "computing P(A|H) (positive diagnosis) instead of P(A|H̄) "
                    "(negative), or inverting the Bayes formula"
                ),
                "typical_error_label": "BAY_invertit",
            },
        ],
    },
}


# Ordre recomanat del camí pilot (topològic en el DAG)
# Camí actiu del pilot. Els 6 problemes del recull PAU 2025-26.
# Ordre numèric per coincidència amb la numeració del recull original
# (no és topològic estricte; PAU-02 i PAU-03 són `nivell` 2 i els
# altres `nivell` 3). El topològic real és:
# Laplace simple (PAU-04 pas 1) → prob. total (PAU-02, PAU-03 pas 2,
# PAU-05 pas 2, PAU-06 pas 3) → Bayes (PAU-02 pas 3, PAU-03 pas 3,
# PAU-05 pas 3, PAU-06 pas 4) → binomial (PAU-01 pas 3, PAU-04 pas 3-4,
# PAU-05 pas 4, PAU-06 pas 1). Veure DAG.md.
PILOT_PATH = [
    "PROB-PAU-01",
    "PROB-PAU-02",
    "PROB-PAU-03",
    "PROB-PAU-04",
    "PROB-PAU-05",
    "PROB-PAU-06",
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
