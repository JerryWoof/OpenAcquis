# Changelog

All notable changes to OpenAcquis. Versions follow [Semantic Versioning](https://semver.org/); while the
version is 0.x, the command-line options and data formats can still change.

## 0.3.0 (2026-10-12)

First alignment: NIS2 × Czechia, provision by provision, scored against the official correlation table.

### Added
- `openacquis align <CELEX> <country>` (`openacquis.align`): directive text in the country's language plus the
  transposing act(s) (default for NIS2 × CZ: 264/2025 Sb. plus any act the evaluation set cites) -> candidates ->
  optional judgments -> predicted links, **gap candidates** (EU provisions with no link) and **extra-rule
  candidates** (provisions of the transposing act that match nothing). JSON export keeps IDs, scores, texts and
  URLs; everything is `status: "candidate"`. Options: `--embedder lexical|e5|e5-sym|bge-m3`,
  `--judge none|rule|anthropic`, `--threshold`, `-k`, `--judge-k`, `--acts`, `--version-date`, `-o`.
- `openacquis eval nis2-cz` (`openacquis.evaluate`): recall@k and MRR of candidate retrieval at four levels
  (provision, odstavec, §, article -> §), precision / recall / F1 of predicted links at a threshold (all and
  "in scope"), a threshold sweep with 2-fold cross-validation, coverage of gap and extra-rule candidates, and
  judge agreement. Options: `--embedder`, `--k 1,5,10`, `--judge`, `--errors N`, `-f json`, `-o`.
  Results: [docs/evaluation.md](docs/evaluation.md).
- Evaluation set `data/eval/nis2_cz.jsonl` (`openacquis.evalset`, `scripts/build_evalset.py`): 569 links from the
  rozdílová tabulka of the Cybersecurity Act bill (tisk 759/0), read from Word `.doc` with the stdlib.
- Candidate matching (`openacquis.match`): stdlib lexical embedder (character n-grams, light Czech stemmer) and
  optional `SentenceTransformerEmbedder` (multilingual-e5-base, bge-m3) with an SQLite vector cache; roll-up from
  points to paragraphs and articles; JSONL export with evidence.
- Judge (`openacquis.judge`, [docs/judge.md](docs/judge.md)): `AnthropicJudge` (structured output, verified
  verbatim evidence quotes, prompt caching, disk cache, Message Batches), keyless `RuleJudge`, `FakeJudge`.
- Website: `alignment.html?id=32022L2555_cz`, every NIS2 paragraph with the official correlation table and the top
  machine suggestion, texts side by side with links to EUR-Lex and e-Sbírka; linked from the home page and the NIS2
  page. Built with the lexical embedder; falls back to the committed `site/static-data/32022L2555_cz.json` when
  texts cannot be fetched (`build_site.py --write-static` refreshes it).
- Optional extras in `pyproject.toml`: `embeddings` (sentence-transformers) and `llm` (anthropic).
- Docs: `docs/evaluation.md`, `docs/judge.md`, `docs/README.md` (index).

### Results (NIS2 × 264/2025 Sb., lexical)
- A provision listed in the table is among the top 5 candidates for 56.3 % of the 197 gold NIS2 provisions (top 1:
  35.0 %); the right § is in the top 5 for 78.7 %, and for whole articles 90.9 %. multilingual-e5-base: 44.2 %,
  73.6 %, 90.9 %.
- Predicted links at threshold 0.20: precision 0.318 (0.414 in scope), recall 0.130 of gold pairs (a link counts
  for a gold provision only when predicted on that provision or one of its parts).

### Changed
- Version 0.3.0; User-Agent `OpenAcquis/0.3`.

### Known limitations
- The LLM judge has not been run against the evaluation set (no API key yet); the rule judge does not help.
- Gap candidates are mostly false alarms so far (66 of 118 are listed in the official table).
- Only NIS2 × Czechia, and only the Cybersecurity Act; the table describes the 2024 bill.

## 0.2.0 (2026-10-12)

Texts in: directive texts and Czech law, split into provisions with stable IDs.

### Added
- `openacquis text <CELEX> --lang <xx>`: a directive's text in any of the 24 EU languages, split into
  articles, paragraphs, points, sub-points and indents (`--recitals` to include recitals; annexes are one provision
  each). Options: `--article N`, `--id art23.par4`, `--refresh`, `-f text|json|jsonl`.
- `openacquis law cz <ref>`: a Czech act from e-Sbírka (current consolidated version, or `--version-date`),
  split into část / hlava / díl / § / odst. / písm. / bod / příloha / footnotes. Options: `--section N`,
  `--article N` (články, arabic or roman: `-a II`), `--id par12.odst2`, `-f text|json|jsonl`.
- `openacquis.provisions`: the shared `Document` / `Provision` data model and the ID scheme
  (`eu:32022L2555:cs:art23.par4.pt_a`, `cz:264/2025:par12.odst2.pism_b`). See `docs/provisions.md`.
- `openacquis.eurlex`: downloads texts from CELLAR (XHTML, falling back to plain HTML for older acts; EUR-Lex is a
  fallback only, because it serves scripts a bot challenge) and parses them. Greek and Bulgarian point letters
  map to Latin IDs (`α)` -> `pt_a`), annexes without `anx_` anchors are found by their title, and text quoted
  by an amending act stays text. Checked live: NIS2 gives 509 provisions with identical IDs in BG, CS, DE, EL,
  EN, HU, LV, PL, RO and SK; older plain-HTML acts such as 98/83/EC and 2000/31/EC parse too.
- `openacquis.adapters.cz`: e-Sbírka adapter (public JSON API at `e-sbirka.gov.cz/sbr-cache`, no key), with
  paging, an on-disk cache and a delay between requests. Checked live: Act 264/2025 Sb. (736 provisions), Civil
  Code 89/2012 Sb. (7,823), Constitution 1/1993 Sb.
- Website for https://jerrywoof.github.io/OpenAcquis/: a static GitHub Pages site showing the notified national
  measures for 10 directives by member state. `scripts/build_site.py` builds its data (`--offline` uses bundled
  samples); `.github/workflows/pages.yml` runs the offline tests, then rebuilds and deploys it on push to `main`
  and weekly. See `docs/website.md`.
- Docs: `docs/provisions.md`, `docs/website.md`, a guide to writing a country adapter in `CONTRIBUTING.md`,
  and the endpoints actually used in `docs/data-sources.md`.

### Changed
- User-Agent is now `OpenAcquis/0.2 (+https://github.com/JerryWoof/OpenAcquis)`.
- The `live` pytest marker now covers every public server (CELLAR, e-Sbírka), not only the SPARQL endpoint.

### Known limitations
- Matching EU provisions to national ones is not there yet (v0.3).
- A subparagraph that follows a list of points is stored in the paragraph's own text.
- EU provisions have no individual ELI, and the EU `Document.date` is empty.
- The e-Sbírka API is undocumented and may change; the open-data dumps are the fallback.
- Text that a Czech amending act inserts into another law stays in the amending point.

## 0.1.0 (2026-10-12)

Act-level map.

### Added
- CELLAR SPARQL client (`openacquis.cellar`): directive metadata (title, date, transposition deadlines) and the
  national implementing measures that member states have notified.
- `openacquis summary <CELEX>`: title, deadline and number of measures for each member state.
- `openacquis measures <CELEX> [--country CZ] [-f table|json|csv]`: list of notified measures.
- Member state codes (alpha-2 / alpha-3), offline tests, CI on Python 3.10, 3.12 and 3.13.
