# How OpenAcquis works

## 1. Which national acts belong to a directive
Member states notify the Commission of the measures they adopt to transpose a directive. The EU Publications Office stores these as *national implementing measures* in CELLAR, the database behind EUR-Lex, queryable via public SPARQL. Each record has the country, the national reference (e.g. `264/2025`), the act type, the official journal and dates, and usually the national title.

This is **act-level** information. One directive often maps to dozens of national acts (NIS2 → 83 Czech measures), and one national act can implement many directives.

## 2. Texts
- Directive: EUR-Lex publishes it in all 24 official languages. Each country is compared with the directive **in its own language**, so no translation step is needed.
- National law: consolidated texts from national portals (see [data-sources.md](data-sources.md)).

## 3. Provisions
Both sides are split into the smallest citable units (article / paragraph / point; § / odstavec / písmeno). Each unit gets a stable ID.

## 4. Matching
1. **Candidates**: multilingual embeddings find the national provisions most similar in meaning to each directive provision, restricted to the notified acts.
2. **Judge**: an LLM reads each candidate pair and classifies it:
   - `same`: transposed as written
   - `partial`: only part of the obligation
   - `different_value`: a parameter differs (deadline, threshold, fine, scope)
   - `stricter`: goes beyond the directive (gold-plating candidate)
   - `option`: records which choice the country made where the directive says "Member States may…"
3. **Leftovers**: directive provisions with no match are *gap candidates*; provisions in transposing acts that match nothing are *extra-rule candidates*.

Older academic prototypes relied on near-identical wording and failed when countries rephrased or restructured. Modern multilingual models match meaning, which is what makes this feasible now.

## 5. Human review and evaluation
- Czech government bills must include official correlation tables (*rozdílové tabulky*) that pair Czech provisions with EU provisions (Legislative Rules of the Government). They give a human-labelled test set to measure precision and recall before extending to other countries.
- Reviewers confirm or correct suggestions; only reviewed results go into the published dataset.

## 6. Outputs
Article × country matrix, side-by-side texts, open dataset (CC-BY-4.0), API, and change alerts.
