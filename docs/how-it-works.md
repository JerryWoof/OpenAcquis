# How OpenAcquis works

## 1. Which national acts belong to a directive (v0.1, working)
Member states notify the Commission of the measures they adopt to transpose a directive. The EU Publications Office stores these as *national implementing measures* in CELLAR, the database behind EUR-Lex, queryable via public SPARQL. Each record has the country, the national reference (e.g. `264/2025`), the act type, the official journal and dates, and usually the national title.

This is **act-level** information. One directive often maps to dozens of national acts (NIS2 → 83 Czech measures), and one national act can implement many directives.

## 2. Texts (v0.2; four countries since v0.4)
- **Directive**: the EU publishes it in all 24 official languages. Each country is compared with the directive **in its own language**, so no translation step is needed. `openacquis.eurlex` asks CELLAR for the XHTML of the requested language (the same markup EUR-Lex shows) and falls back to plain HTML for older acts. EUR-Lex itself blocks scripted downloads with a bot check, so it is only a fallback.
- **National law**: consolidated texts from national portals, one adapter per country in `openacquis.adapters` (see [data-sources.md](data-sources.md)), registered in `openacquis.adapters.ADAPTERS`:
  - Czechia: e-Sbírka, version by version (`cz`).
  - Slovakia: Slov-Lex, version by version (`sk`).
  - Austria: RIS open data, text in force on any date (`at`).
  - Germany: gesetze-im-internet.de, current text only, with NeuRIS for ELIs (`de`).

  Each adapter also maps the measures CELLAR lists to acts it can fetch (`openacquis measures <CELEX> -c SK --map`).

Try it: `openacquis text 32022L2555 --lang cs --article 23`, `openacquis law cz 264/2025 --section 12`, `openacquis law sk 69/2018 -s 20`, `openacquis law at "NISG 2026" -s 32`, `openacquis law de bsig_2025 -s 30`.

## 3. Provisions (v0.2, working)
Both sides are split into the smallest citable units: article / paragraph / point / sub-point / indent (plus recitals and annexes) for EU acts; část / hlava / díl / § / odstavec / písmeno / bod / příloha for Czech law, and the equivalent units in Slovak, Austrian and German law (§ / ods. / písm.; § / Abs. / Z / lit.; § / Abs. / Nr. / Buchst.). Each unit gets a stable, path-like ID such as `eu:32022L2555:cs:art23.par4.pt_a` or `cz:264/2025:par12.odst2.pism_b`, and a link to the official text. The shared data model and ID scheme are in [provisions.md](provisions.md).

Checked against the real texts: NIS2 gives the same 509 provisions in Bulgarian, Czech, English, German, Greek, Hungarian, Latvian, Polish, Romanian and Slovak, with identical IDs apart from the language code (46 articles, 189 paragraphs, 243 points, 28 sub-points, 3 annexes), so EU provisions line up across languages by ID. The Czech Cybersecurity Act 264/2025 Sb. gives 736 provisions (73 §).

## 4. Matching (v0.3; NIS2 × CZ, SK, AT, DE since v0.4)
`openacquis align 32022L2555 cz` (or `sk`, `at`, `de`) runs the whole pipeline (`openacquis.align`). The directive is fetched in the language of the national act (cs, sk, de, de), and the main transposing act comes from `DEFAULT_ACTS`. `--measures` adds every notified measure the adapter can fetch.

1. **Candidates** (`openacquis.match`): every EU provision with its own text (paragraph lead-ins, points,
   sub-points: 473 units for NIS2) is compared with every unit of the national act (629 for 264/2025 Sb.).
   Each unit is read with its headings and the beginning of the enclosing provision, so a point `a)` is read
   with its lead-in. Two ways to score similarity:
   - `lexical` (stdlib): TF-IDF over character 3–5-grams of lower-cased, stemmed, diacritic-free
     words (the light Czech stemmer is used for Czech and Slovak, none for German). Both sides are in the
     same language, so no translation is needed.
   - `hybrid-lex` (stdlib, v0.4): lexical + lexical on the provision's own text + BM25, fused per EU unit,
     with a structural prior. It is the default without extras and what the website uses.
   - `e5` / `bge-m3` / `hybrid` (optional extra `openacquis[embeddings]`): multilingual dense embeddings,
     cached on disk. `hybrid` (lexical + e5) is the default when the extra is installed.

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
3. **Links and leftovers** (`openacquis.leftovers`, v0.4):
   - A predicted link is the best candidate at or above a per-method threshold (lexical 0.20,
     `hybrid-lex` 0.17, `hybrid` with e5 0.14, `hybrid-rrf` 0.03), or a pair the judge labels related.
   - *Gap candidates* are directive provisions with no link whose best score stays clearly below the
     threshold. Final provisions, provisions addressed only to the Commission or ENISA, and lead-ins
     or points of a linked list are set aside first. `hybrid-rrf` gives no gap list: its scores carry
     only ranks, so a low score does not mean "nothing similar".
   - *Extra-rule candidates* are units of the main transposing act, in a § with no link at all, that
     match nothing. Definitions, penalty lists, transitional, repeal and entry-into-force clauses and
     amendments of other acts are set aside first.

   Both are reading lists for a reviewer, not findings. Against the Czech table, the v0.3 lists were
   noise: 66 of 118 gap candidates were listed as transposed. v0.4 reports 16 gap candidates, 3 of them
   in the table (12 of the 16 in Art. 32), and 33 extra-rule candidates, 6 of them cited
   ([evaluation.md](evaluation.md)).

Older academic prototypes relied on near-identical wording and failed when countries rephrased or
restructured. Modern multilingual models match meaning. On NIS2 × Czechia, however, the simple lexical
baseline still beats multilingual-e5-base, because the Czech act often reuses the directive's wording.

## 5. Evaluation and human review (v0.3 evaluation; v0.4 review queue)
- Czech government bills must include official correlation tables (*rozdílové tabulky*) that pair Czech
  provisions with EU provisions (Legislative Rules of the Government). The table of the Cybersecurity Act
  bill became the first test set: `data/eval/nis2_cz.jsonl`, 569 links.
- `openacquis eval nis2-cz` scores the pipeline against it:
  - A listed provision is among the top 5 candidates for 56 % of NIS2 provisions, and the right § for 79 %.
  - Precision of the predicted links is 0.32, or 0.42 on provisions the table covers.

  Numbers, method and errors: [evaluation.md](evaluation.md).
- Reviewers confirm, reject or correct suggestions on the website's review page. Each verdict is a JSON
  record sent as a GitHub issue or a pull request into `data/reviews/` ([review.md](review.md)). Only
  reviewed links and official tables go into the published dataset ([dataset.md](dataset.md)).

## 6. Outputs
Article × country matrix, side-by-side texts, open dataset (CC-BY-4.0), API, and change alerts.

The public website, https://jerrywoof.github.io/OpenAcquis/, is published by GitHub Actions. It shows:

- the act-level data from step 1 for ten directives;
- for NIS2, an article × country matrix (`matrix.html`) for Czechia, Slovakia, Austria and Germany;
- one provision-level page per country (`alignment.html?id=32022L2555_sk`), with gap and extra-rule
  candidates and, for Czechia, the official correlation table;
- the review page;
- the dataset downloads.

See [website.md](website.md). An API and change alerts come later.
