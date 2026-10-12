# Roadmap

## v0.1: act-level map (done)
- [x] CELLAR SPARQL client: directive metadata and notified national measures
- [x] CLI: `summary`, `measures` (table / JSON / CSV)

## v0.2: texts in (done)
- [x] Fetch directive text per language and split into provisions (article / paragraph / point / sub-point / indent; recitals and annexes optional): `openacquis text`, `openacquis.eurlex`
  - Source is CELLAR XHTML (same markup as EUR-Lex), with plain HTML for older acts. EUR-Lex itself answers scripts with a bot challenge, so it is only a fallback. Formex is not used.
  - Checked live: NIS2 gives 509 provisions with identical IDs in BG, CS, DE, EL, EN, HU, LV, PL, RO and SK (Greek and Cyrillic point letters map to Latin IDs). Older plain-HTML acts (98/83/EC, 2000/31/EC) and CONVEX texts without annex anchors (93/13/EEC) parse; text quoted by amending acts stays text.
  - Limitations: an annex is a single provision; a subparagraph after a list of points is stored in the paragraph's text; EU provisions have no individual ELI.
- [x] Czech adapter: consolidated texts from e-Sbírka, split into část / hlava / díl / § / odst. / písm. / bod / příloha: `openacquis law cz`, `openacquis.adapters.cz`
  - Uses the public JSON API behind e-sbirka.gov.cz (no key, but undocumented); a specific dated version (or the text as promulgated) can be requested. Checked live: 264/2025 (736 provisions), 89/2012 Civil Code (7,823), 1/1993 Constitution.
  - Limitations: text inserted by amending acts stays inside the amending point; annex tables are flattened; the open-data dumps are documented but not used yet.
- [x] Common provision data model with stable IDs (ELI where available): `openacquis.provisions`, see [docs/provisions.md](docs/provisions.md)
  - ELI is kept per provision for Czech law; for EU acts only at document level.

## Website (seed of v1.0) (done)
- [x] Static site for GitHub Pages, rebuilt weekly by GitHub Actions, to be published at https://jerrywoof.github.io/OpenAcquis/ (see [docs/website.md](docs/website.md); the first deploy needs Settings → Pages → Source = GitHub Actions)
  - Act-level only for now: notified national measures per member state for 10 directives (NIS2, Accessibility Act, Pay Transparency, CSRD, Whistleblower, CER, Copyright DSM, Work-Life Balance, Minimum Wages, EECC), with a country grid and a searchable table of measures.

## v0.3: first alignment (NIS2 × Czechia) (done, judge run pending)
Results and error analysis: [docs/evaluation.md](docs/evaluation.md).
- [x] Evaluation set from Czech *rozdílové tabulky* (official correlation tables attached to government bills, psp.cz): `data/eval/nis2_cz.jsonl`, `openacquis.evalset`
  - Table of the Cybersecurity Act bill (sněmovní tisk 759/0, 2024), read from Word `.doc` with the stdlib: 569 links between 197 NIS2 provisions (33 articles) and 243 provisions of Act 264/2025 Sb.; 100 % of IDs resolve (6 renumbered between bill and act, confirmed by text).
  - Not covered yet: the companion bill (tisk 760) and the implementing decrees.
- [x] Candidate matching (Czech directive text vs Czech law): `openacquis.match`
  - Stdlib lexical baseline (character n-grams, Czech stemming) and optional dense embeddings (`openacquis[embeddings]`, multilingual-e5-base / bge-m3) with an on-disk vector cache.
  - Against the table: a listed provision is in the top 5 for 56.3 % of gold NIS2 provisions (lexical; e5: 44.2 %), the right § for 78.7 % (e5: 73.6 %), and for whole articles 90.9 %. Top-1 is right for 35.0 %. Lexical beats e5 here.
- [x] LLM judge: same / partial / different parameter / stricter / option chosen; `missing` per EU provision: `openacquis.judge`, [docs/judge.md](docs/judge.md)
  - Claude with structured output, verbatim evidence quotes checked against the texts, prompt and result caching, batches; plus a keyless `RuleJudge`.
  - **Not yet run against the evaluation set** (needs `ANTHROPIC_API_KEY`; estimated about $8 for NIS2 × CZ). The rule judge was run and does not improve precision (0.225 vs 0.318).
- [x] Report precision and recall against the evaluation set: `openacquis eval nis2-cz`, `openacquis.evaluate`
  - Predicted links (best candidate per NIS2 unit, lexical, threshold 0.20): precision 0.318 (0.414 on provisions the table covers), recall 0.130 over 568 gold pairs; at § level precision 0.437 (0.568), recall 0.325. A link counts for a gold provision only when predicted on that provision or one of its parts.
- [x] Pipeline with gap and extra-rule candidates: `openacquis align 32022L2555 cz`, `openacquis.align`
  - Not yet a usable signal: 66 of the 118 gap candidates are listed in the official table.
- [x] Website: [NIS2 × Czechia provision by provision](https://jerrywoof.github.io/OpenAcquis/alignment.html?id=32022L2555_cz), official correlation next to the top machine suggestion, texts side by side.

## v0.4: four countries (done; LLM judge run and reviews pending)
Results: [docs/evaluation.md](docs/evaluation.md) ("v0.4" sections).
- [x] Adapters for Slovakia (Slov-Lex), Austria (RIS OGD API), Germany (gesetze-im-internet.de / NeuRIS): `openacquis law sk|at|de`, registry `openacquis.adapters.ADAPTERS`
  - Live counts:
    - SK: Act 69/2018 Z. z., 873 provisions; any dated version.
    - AT: NISG 2026, 607 provisions; text in force on any date; IDs by Gesetzesnummer.
    - DE: BSIG, 666 provisions; current text only.
  - Notified NIS2 measures mapped to fetchable acts (`measures --map`, `align --measures`): SK 9 of 9, AT 8 of 8, DE 1 of 20.
  - Limitations:
    - DE: 17 of the 20 German measures are Land law, which gesetze-im-internet.de does not carry; the federal laws the transposition act amends are not listed by CELLAR.
    - AT: RIS needs the `tls` extra (certifi) on some systems.
    - Annexes published only as PDF have no text.
- [x] Gap and gold-plating candidates good enough to review (`openacquis.leftovers`)
  - How: scope filters (EU final provisions and units addressed only to the Commission or ENISA; national boilerplate), a gap margin below the link threshold, structural rules (lists, § with no link, main act only), grouping per article and per §.
  - NIS2 × CZ, `hybrid-lex`, gap candidates: 16, of which 13 are not in the official table. That is 81 % (95 % interval 57–93 %), against a 28 % base rate; v0.3 had 118 at 44 %. With the margin chosen out of sample: 64 % (split by article parity) or 76 % (leave one article out).
  - Extra-rule candidates: 33 units in 11 §§, 82 % not cited (base rate 50 %; v0.3: 195 at 75 %).
  - Limitations:
    - small, clustered samples (12 of the 16 gaps are Art. 32(2) and (7); outside Art. 32 only 1 of 4 is not in the table);
    - `hybrid-rrf` gives links but no gap list (rank-only scores);
    - thresholds tuned on Czech only;
    - "not in the table" is not "not transposed";
    - extra rules are mostly national procedure, not gold-plating.
- [ ] Better matching: lexical + dense hybrid, following cross-references (sanctions), short list items; run the LLM judge
  - [x] Hybrid matching (`hybrid-lex` stdlib, the default without extras: MRR 0.445 → about 0.50 cross-validated; `hybrid` with e5: R@5 0.563 → 0.635). Body-only channels fix short list items (Art. 21(2)(i): rank 7 → 1).
  - [ ] Following cross-references for sanctions (Art. 36 still missed).
  - [ ] LLM judge run (needs `ANTHROPIC_API_KEY`).
- [x] Review queue for human reviewers; reviewed results exported as an open CC-BY dataset
  - `review.html` queue, review records in `data/reviews/`, `openacquis review validate|add|stats` ([docs/review.md](docs/review.md)).
  - GitHub issue form and a validate-and-comment workflow. It does not auto-commit, by design.
  - `openacquis dataset build` writes `links.csv` / `links.jsonl` / `datapackage.json` (CC-BY-4.0), also published on the website ([docs/dataset.md](docs/dataset.md)).
  - Today: 568 official-table links (NIS2 × CZ); no reviews yet. Release attachments come with the first tagged release.

## v0.5: v1.0 preview (done; judge run and reviews pending)
See [CHANGELOG](CHANGELOG.md#050-2026-10-12-v10-preview). Registry of 12 directives, 46 alignment pages, six held-out
official tables (CZ: CER, Accessibility Act, Whistleblower; SK: NIS2, CER, Pay Transparency), `openacquis eval <CELEX> <cc>`,
static API, change alerts, batch judge.

## v1.0: public observatory (v0.5 = preview; what is done and what is not)
- [x] Website: article × country matrix with side-by-side source texts
  - [x] Seed: [NIS2 × CZ, SK, AT, DE](https://jerrywoof.github.io/OpenAcquis/matrix.html?celex=32022L2555) (v0.4)
  - [x] More directives and countries (v0.5): all 12 registry directives × CZ, SK, AT, DE (46 alignment pages, 12 matrices),
    "not notified" (baseline) and "nothing to align" states, official columns for 7 pairs (CZ and SK)
  - [ ] Reviewed cells shown as such: the review pages mark reviewed suggestions, but there are **0 reviews** so far, so no
    cell is reviewed yet
  - [ ] Alignments other than NIS2 are precomputed (`scripts/build_alignments.py`), not refreshed by the weekly build
- [x] Public API and dataset releases (v0.5): static JSON API with OpenAPI and JSON Schemas ([docs/api.md](docs/api.md)),
  `data.html`, release workflow attaching dataset and API snapshots, `CITATION.cff` / `.zenodo.json`
  - [ ] First tagged release and Zenodo DOI (needs the owner to switch the repository on at Zenodo)
  - Dataset: 2,736 links from 7 official tables; 0 reviewed links
- [x] 10–20 high-profile directives at provision level: 12 (v0.5), registry `data/directives.json`
  - Quality checked against official tables for 7 pairs only; [held-out results](docs/evaluation.md#v05-held-out-generalisation-six-more-official-tables):
    R@5 0.600–0.902 with thresholds tuned on NIS2 × CZ only. Austria and Germany have no table to check against.
  - [ ] CSRD (consolidated EU references, auditors act) and Work-Life Balance stay act level only
- [x] Alerts when a national law or directive changes (v0.5): weekly workflow, GitHub issues, [Atom feed](https://jerrywoof.github.io/OpenAcquis/feed.xml) ([docs/alerts.md](docs/alerts.md))
  - Seeded with 365 watched items; first live weeks still to come. National acts not yet on the list are only caught once CELLAR lists them
- [ ] LLM judge run: batch runner and dispatch-only workflow are ready (v0.5, about $53 for all 12 directives × 4 countries with
  Sonnet 5.5 batches, estimate; `--max-cost` caps the worst case, no cache hits and every answer at `--max-tokens`,
  about 8 times the typical estimate: $13.90 for NIS2 × CZ); **not run**, it needs an API key in the repository's `llm` environment
- [ ] Human review at scale: review pages exist for every pair; 0 reviews so far

## Later
- Legislative footprint: trace wording from public consultation responses and EP amendments into final law
- All directives, all member states; candidate countries tracking alignment with the acquis
