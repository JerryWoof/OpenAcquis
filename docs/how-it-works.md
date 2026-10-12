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

## 4. Matching (v0.3, working for NIS2 × Czechia)
`openacquis align 32022L2555 cz` runs the whole pipeline (`openacquis.align`):

1. **Candidates** (`openacquis.match`): every EU provision with its own text (paragraph lead-ins, points,
   sub-points: 473 units for NIS2) is compared with every unit of the national act (629 for 264/2025 Sb.).
   Each unit is read with its headings and the beginning of the enclosing provision, so a point `a)` is read
   with its lead-in. Two ways to score similarity:
   - `lexical` (default, stdlib): TF-IDF over character 3–5-grams of lower-cased, stemmed, diacritic-free
     Czech words. Both sides are in Czech, so no translation is needed.
   - `e5` / `bge-m3` (optional extra `openacquis[embeddings]`): multilingual dense embeddings, cached on disk.

   The top *k* national units per EU unit are the candidates. Scores are similarities, not probabilities,
   and comparable only within one method.
2. **Judge** (optional, `openacquis.judge`, see [judge.md](judge.md)): a model reads each of the top
   candidate pairs and labels it, quoting both texts verbatim as evidence:
   - `same`: transposed as written
   - `partial`: only part of the obligation
   - `different_value`: a parameter differs (deadline, threshold, fine, scope)
   - `stricter`: goes beyond the directive (gold-plating candidate)
   - `option`: records which choice the country made where the directive says "Member States may…"
   - `unrelated`

   `missing` is decided per EU provision, when none of its candidates is related. `--judge rule` is a
   keyless heuristic baseline; `--judge anthropic` uses Claude and has not been run against the evaluation
   set yet.
3. **Links and leftovers**:
   - A predicted link is the best candidate at or above a per-method threshold (lexical 0.20), or a pair
     the judge labels related.
   - Directive provisions with no link are *gap candidates*.
   - Provisions of the transposing act that match nothing are *extra-rule candidates*.

   Both are reading lists for a reviewer, not findings. In the first evaluation, more than half of the gap
   candidates are provisions the government lists as transposed.

Older academic prototypes relied on near-identical wording and failed when countries rephrased or
restructured. Modern multilingual models match meaning. On NIS2 × Czechia, however, the simple lexical
baseline still beats multilingual-e5-base, because the Czech act often reuses the directive's wording.

## 5. Evaluation and human review (v0.3 evaluation working; review v0.4, planned)
- Czech government bills must include official correlation tables (*rozdílové tabulky*) that pair Czech
  provisions with EU provisions (Legislative Rules of the Government). The table of the Cybersecurity Act
  bill became the first test set: `data/eval/nis2_cz.jsonl`, 569 links.
- `openacquis eval nis2-cz` scores the pipeline against it:
  - A listed provision is among the top 5 candidates for 56 % of NIS2 provisions, and the right § for 79 %.
  - Precision of the predicted links is 0.32, or 0.42 on provisions the table covers.

  Numbers, method and errors: [evaluation.md](evaluation.md).
- Reviewers confirm or correct suggestions; only reviewed results go into the published dataset (v0.4).

## 6. Outputs
Article × country matrix, side-by-side texts, open dataset (CC-BY-4.0), API, and change alerts.

A first public website will be published at https://jerrywoof.github.io/OpenAcquis/ by GitHub Actions. Today it shows the act-level data from step 1 for ten directives, and one provision-level page: NIS2 × Czechia, with the official correlation table next to the top machine suggestion (`alignment.html?id=32022L2555_cz`). See [website.md](website.md).
