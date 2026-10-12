# How OpenAcquis works

## 1. Which national acts belong to a directive (v0.1, working)
Member states notify the Commission of the measures they adopt to transpose a directive. The EU Publications Office stores these as *national implementing measures* in CELLAR, the database behind EUR-Lex, queryable via public SPARQL. Each record has the country, the national reference (e.g. `264/2025`), the act type, the official journal and dates, and usually the national title.

This is **act-level** information. One directive often maps to dozens of national acts (NIS2 → 83 Czech measures), and one national act can implement many directives.

## 2. Texts (v0.2, working)
- **Directive**: the EU publishes it in all 24 official languages. Each country is compared with the directive **in its own language**, so no translation step is needed. `openacquis.eurlex` asks CELLAR for the XHTML of the requested language (the same markup EUR-Lex shows) and falls back to plain HTML for older acts. EUR-Lex itself blocks scripted downloads with a bot check, so it is only a fallback.
- **National law**: consolidated texts from national portals, one adapter per country in `openacquis.adapters` (see [data-sources.md](data-sources.md)). Czechia is first: `openacquis.adapters.cz` reads e-Sbírka, the official Collection of Laws, version by version.

Try it: `openacquis text 32022L2555 --lang cs --article 23` and `openacquis law cz 264/2025 --section 12`.

## 3. Provisions (v0.2, working)
Both sides are split into the smallest citable units: article / paragraph / point / sub-point / indent (plus recitals and annexes) for EU acts; část / hlava / díl / § / odstavec / písmeno / bod / příloha for Czech law. Each unit gets a stable, path-like ID such as `eu:32022L2555:cs:art23.par4.pt_a` or `cz:264/2025:par12.odst2.pism_b`, and a link to the official text. The shared data model and ID scheme are in [provisions.md](provisions.md).

Checked against the real texts: NIS2 gives the same 509 provisions in Bulgarian, Czech, English, German, Greek, Hungarian, Latvian, Polish, Romanian and Slovak, with identical IDs apart from the language code (46 articles, 189 paragraphs, 243 points, 28 sub-points, 3 annexes), so EU provisions line up across languages by ID. The Czech Cybersecurity Act 264/2025 Sb. gives 736 provisions (73 §).

## 4. Matching (v0.3, planned)
1. **Candidates**: multilingual embeddings find the national provisions most similar in meaning to each directive provision, restricted to the notified acts.
2. **Judge**: an LLM reads each candidate pair and classifies it:
   - `same`: transposed as written
   - `partial`: only part of the obligation
   - `different_value`: a parameter differs (deadline, threshold, fine, scope)
   - `stricter`: goes beyond the directive (gold-plating candidate)
   - `option`: records which choice the country made where the directive says "Member States may…"
3. **Leftovers**: directive provisions with no match are *gap candidates*; provisions in transposing acts that match nothing are *extra-rule candidates*.

Older academic prototypes relied on near-identical wording and failed when countries rephrased or restructured. Modern multilingual models match meaning, which is what makes this feasible now.

## 5. Human review and evaluation (v0.3–v0.4, planned)
- Czech government bills must include official correlation tables (*rozdílové tabulky*) that pair Czech provisions with EU provisions (Legislative Rules of the Government). They give a human-labelled test set to measure precision and recall before extending to other countries.
- Reviewers confirm or correct suggestions; only reviewed results go into the published dataset.

## 6. Outputs
Article × country matrix, side-by-side texts, open dataset (CC-BY-4.0), API, and change alerts.

A first public website will be published at https://jerrywoof.github.io/OpenAcquis/ by GitHub Actions. Today it shows the act-level data from step 1 for ten directives; see [website.md](website.md).
