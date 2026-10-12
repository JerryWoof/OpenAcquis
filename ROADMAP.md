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

## v0.4: four countries
- [ ] Adapters for Slovakia (Slov-Lex), Austria (RIS OGD API), Germany (gesetze-im-internet.de / NeuRIS)
- [ ] Gap and gold-plating candidates from unmatched provisions that are good enough to review (first version in v0.3; too many false alarms so far)
- [ ] Better matching: lexical + dense hybrid, following cross-references (sanctions), short list items; run the LLM judge
- [ ] Review queue for human reviewers; reviewed results exported as an open CC-BY dataset

## v1.0: public observatory
- [ ] Website: article × country matrix with side-by-side source texts (the site above is the starting point)
- [ ] Public API and dataset releases
- [ ] 10–20 high-profile directives (NIS2, Accessibility Act, Pay Transparency, CSRD, Whistleblower) at provision level (10 already listed act-level on the website)
- [ ] Alerts when a national law or directive changes

## Later
- Legislative footprint: trace wording from public consultation responses and EP amendments into final law
- All directives, all member states; candidate countries tracking alignment with the acquis
