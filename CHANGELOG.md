# Changelog

All notable changes to OpenAcquis. Versions follow [Semantic Versioning](https://semver.org/); while the
version is 0.x, the command-line options and data formats can still change.

## 0.5.0 (2026-10-12): v1.0 preview

Twelve directives × four countries at provision level, six held-out official correlation tables, a static API,
change alerts and a resumable batch judge.

### Added
- **Directive registry** `data/directives.json` and `openacquis.directives`: 12 directives (NIS2, CER,
  Accessibility Act, Whistleblower, Pillar Two, Copyright DSM, Representative Actions, Pay Transparency, Minimum
  Wages, Single-Use Plastics, EECC, Women on Boards) with the main act per country, status `ok` / `baseline`
  (not notified; aligned against existing law: Pay Transparency in AT and DE) / `none` (nothing to align:
  Minimum Wages in AT, Women on Boards in CZ), hand overrides and notes. `openacquis directives` prints it.
  `align.default_acts` / `default_version` fall back to it.
- **`scripts/build_alignments.py`**: aligns every registry pair (42 new, plus the 4 NIS2 ones) and writes the
  static copies the site uses; parallel per country, one request at a time per portal, cached and resumable.
- **Evaluation sets** (`openacquis.goldsets`, one registry used by `eval`, the dataset, the site and the API):
  - Czech: CER (229 links), Accessibility Act (146), Whistleblower (685) from psp.cz, with a generalised
    importer (`scripts/build_evalset.py`: multi-CELEX cells, CELEX typos, consolidated references, multi-act
    tables, `ČÁST` amendment points, `--celex/--tisk` discovery).
  - Slovak: NIS2 (470), CER (414), Pay Transparency (232) from NR SR *tabuľky zhody*
    (`openacquis.evalset_sk`, `scripts/build_evalset_sk.py`), keeping method, compliance and gold-plating
    columns, and negative records ("nothing transposes this"), which are never used as links.
- **`openacquis eval <CELEX> <cc>`** (or a set name: `nis2-cz`, `cer-cz`, `eaa-cz`, `whistleblower-cz`,
  `nis2-sk`, `cer-sk`, `pay-sk`; `eval --list`). By default it searches and scores the act each table was
  checked against, in that version; `--all-acts` uses every act a table cites. Reports say whether a set is
  held out.
- **Held-out results** in `docs/evaluation.md`: with the thresholds tuned on NIS2 × CZ and not retuned,
  `hybrid-lex` R@5 ranges from 0.600 (NIS2 × SK) to 0.902 (Pay × SK), § R@5 from 0.785 to 0.957.
- **Static public API** (`scripts/build_api.py`, `api/openapi.yaml`, 16 JSON Schemas, `docs/api.md`), built and
  schema-checked by the Pages workflow; **release workflow** attaching the dataset and API snapshots;
  `CITATION.cff` and `.zenodo.json`.
- **Change alerts** (`openacquis.alerts`, `scripts/check_changes.py`, `openacquis alerts check|feed`,
  weekly `.github/workflows/alerts.yml`): CELLAR amendments and notified measures, e-Sbírka, Slov-Lex, RIS and
  gesetze-im-internet.de version signals; GitHub issues and an Atom feed (`site/feed.xml`). Seeded state: 365
  items, 0 false alerts on a repeat run.
- **Batch judge** (`openacquis.judge_batch`, `openacquis judge-batch run|status`, dispatch-only
  `.github/workflows/judge.yml`): Message Batches, resumable state, cost estimate and `--max-cost` cap; prompts
  for cz, sk, at and de. **Not run against the API yet** (needs credentials).
- **Website (v1.0 preview):** every registry directive on the home page with per-country chips (official table,
  not notified, nothing to align); a "provision by provision" table on each directive page; alignment, review
  and matrix pages for all 46 pairs (`matrix.html?celex=…` for all 12 directives) with the official column for
  the 7 pairs with a table; "not notified" and "no act aligned" states; `data.html` (dataset, API, feed, CLI);
  a feed link on every page.

### Changed
- `site` builds list every directive offline too (`measures_available: false` without a bundled sample).
  Only NIS2 alignments are computed live by default (`--live-alignments seed|all|none`); the rest come from
  `site/static-data/`. The Pages artifact leaves out `site/static-data/`.
- The dataset now holds 2,736 official-table links from 7 tables (was 568); `status` can also be
  `fully_compatible` / `partially_compatible` / `incompatible` (Slovak tables).
- `make_judge(name, country)` passes the country to `AnthropicJudge` (SK, AT and DE no longer get the Czech
  prompt). `--judge anthropic` no longer insists on `ANTHROPIC_API_KEY`: it needs the `llm` extra and lets
  the SDK find credentials (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, …).
- `evaluate.load_gold(path, acts=None)` drops records without a national provision and can keep one act's links.
- Sonnet 5.5 cache-read price corrected ($0.10/MTok); `AnthropicJudge.cost()` no longer takes `batch`.

### Fixed before release
- **Gold sets.** Czech: a renumbering can no longer land on another ID the same row cites, two IDs of one row
  are never renumbered onto the same act ID, an ID is kept when one of its sentences appears word for word in the
  quote, `odst. 1 až odst. 3` keeps the middle paragraph, `písm. a) až e) a 2` reads `2` as a paragraph, and an
  inserted letter re-aligns the cited letters (Whistleblower: 685 links, 683 distinct pairs). Slovak: `§: 5 P: f
  O: N` cells read as points of the letter, EU qualifiers go to `note`, `partial_negative` flags negatives whose EU
  provision also has links, PDF-only annexes are never "confirmed", negatives of methods D and O are
  `no_national_provision`, and the Constitution's articles resolve (see Slovak adapter). Distinct pairs:
  NIS2 × SK 469, CER × SK 409, Pay × SK 232. Regression tests for each case.
- **Slovak adapter.** The articles of the Constitution (460/1992 Zb., Slov-Lex `ustavnyclanok` units) are read as
  články (`460/1992:cl127.ods1`); before, the whole text fell into 30 heading-level units.
- **Candidates.** National units that only cite the directive or announce its transposition (`Mit diesem
  Bundesgesetz wird … umgesetzt`, Slovak "Zoznam preberaných …" annexes) are no longer candidates, except of the
  directive's Transposition article (`leftovers.transposition_notice`, `MatchResult.masked`; listed in
  `leftovers.national_excluded`). NIS2 × SK R@5 0.595 → 0.600; the other sets are unchanged or better.
- **Extra-rule lists.** None for general codes (registry `"extra_rules": false`: Labour Codes, AktG, AWG 2002,
  copyright acts), for acts of which more than half the units would be candidates (`extra_max_share`), and for
  baseline pairs. Pages say why (`extra_note`, `summary.extra_rule_candidates_suppressed`); the review queues
  drop those items (Minimum Wages × CZ: 1,572 → 66).
- **Pages.** Rows whose official provisions are all in acts that were not searched are `other_act`, no longer
  "differs" (Pay Transparency × SK: 17 rows). Baseline pairs read "<act> was not notified for this directive"
  with the country's own measure count, never "no measure notified"; their review questions ask whether the
  provision *covers* the EU provision. Registry notes are shown in public wording (`public_note`, new registry
  key), secondary acts by their registry label (`act_labels`, new key: UrhG, BFSGV, EWKFondsG). The provision
  table stacks on phones; the matrix marks baseline columns; `data.html` lists every pair.
- **NIS2 × SK** pages use the 2025-01-01 text the evaluation set uses (registry `version`;
  `default_version` falls back to the set's version). Single-Use Plastics × DE keeps EWKFondsG with
  `--from-cellar` (`override`).
- **Dataset and API.** New `relation` field: `existing_law` for reviews of baseline pairs, `transposition` for
  every other row. The API index links `schemas/index.json` (a listing of the schema files) instead of a folder;
  alignment files carry `extra_note`.
- **Alerts.** An empty amendments or measures side of the CELLAR answer for every directive is an outage (old
  fingerprints kept); a single vanished record is a warning and is reported only after 3 runs; `--sources`
  subsets keep the other sources' items; the workflow reads alignment pages from `site/static-data`.
- **Batch judge.** `--max-cost` caps the worst case (no cache hits, every answer at `--max-tokens`), and the
  workflow default is $15; results with unknown IDs still count as spent; a 4xx rejection clears the
  submission marker, other interruptions exit 2 with a message; a `force` workflow input; `status` uses the
  run's saved settings.
- **Workflows.** `pages.yml` has least-privilege permissions; `release.yml` also checks the `CITATION.cff`
  version; `--require-live` (`seed` by default, or `all`) fails only on pairs meant to be computed live.
- All 46 static alignment pages rebuilt with 0.5.0 (`site/static-data/` about 29 MB).

### Known limitations
- No human reviews yet; the LLM judge has not been run (no key in this environment).
- Machine coverage on large general codes (labour codes, AktG, EECC laws) is not quality; only 7 pairs have a
  table to check against, and none in Austria or Germany.
- `site/static-data/` is about 29 MB; the deployed site about 66 MB.

## 0.4.0 (2026-10-12)

Four countries: NIS2 × Czechia, Slovakia, Austria and Germany, with filtered gap and extra-rule candidates, a
review queue and an open dataset.

### Added
- **Country adapters** (stdlib only, injectable fetchers, cache under `$OPENACQUIS_CACHE`, 1 s between requests):
  - `openacquis.adapters.sk` (Slov-Lex). Any consolidated version: by date, by Slov-Lex version id, or the
    version a CELLAR measure names (`69/2018-366/2024`: the latest version that amendment produced which is
    already in force). Group headings apply to every § of the group without its own heading; footnotes quoted
    by amending points stay in the point's text. Act 69/2018 Z. z.: 873 provisions.
  - `openacquis.adapters.at` (RIS OGD search API + XML). Any date. IDs use the RIS Gesetzesnummer. Fetches
    only the cited §§ of a large act (also "§§ 3 und 5", "§§ 3 bis 5"), in document order. In laws whose §§
    sit in Artikel (DSG), Hauptstück and Abschnitt nest inside the Artikel; `at.provision_id(doc, "par4.abs1")`
    finds `art2.par4.abs1`. `ziffernliste` lists become `z` provisions. No ELI for laws promulgated before
    2004 (RIS has none). NISG 2026: 607 provisions.
  - `openacquis.adapters.de` (gesetze-im-internet.de + NeuRIS). Current text only. Abbreviations NeuRIS lacks
    are found in the gesetze-im-internet.de table of contents (EnWG → `enwg_2005`, TKG → `tkg_2021`). Land
    adjectives ("Hamburgisches", "Berliner") are recognised; Runderlass, Ministerialblatt,
    Verwaltungsvorschrift and Amtsblatt titles are administrative measures (`not_found`, no BGBl lookup).
    BSIG: 666 provisions, 324 with an ELI.
  - In AT and DE a later list in the same Absatz whose numbers clash with an earlier one gets `_2` on every
    item (`nr1_2`, `z1_2`).
  - All three map CELLAR's notified NIS2 measures to fetchable acts: SK 9 of 9, AT 8 of 8, DE 1 of 20
    (17 are Land measures).
- **Adapter registry** `openacquis.adapters.ADAPTERS` (code, CELLAR country code, language, portal, client,
  citation style), plus `get_adapter`, `map_measures` and `fetch_measure`.
- **Command line:**
  - `openacquis law sk|at|de <ref>`; `--article` uses `cl` (CZ, SK; roman numerals accepted) or `art` (AT, DE);
    `--section` finds a § inside an Artikel in Austrian laws (`law at DSG -s 4` → `art2.par4`).
  - `openacquis measures <CELEX> -c SK --map`: how each notified measure maps to an act.
  - `openacquis align 32022L2555 sk|at|de`: the directive in the act's language (cs, sk, de, de) and the main
    transposing act. Default acts: 264/2025, 69/2018, 20013065 (NISG 2026), bsig_2025. `--measures` adds
    every notified measure the adapter can fetch; `--gap-margin` sets the gap margin.
- **Hybrid matching** (`openacquis.match`): `BM25Embedder`, `HybridEmbedder` (weighted z-score or
  reciprocal-rank fusion, body-only channels), `StructuralPrior`, and the presets `hybrid-lex` (stdlib),
  `hybrid` (lexical + multilingual-e5) and `hybrid-rrf`. `make_hybrid(..., stem=False)` is for German.
  `hybrid-rrf` has its own threshold (0.03) and gives links but no gap list: its scores carry only ranks
  (`align.rank_only`, `gap_note` in the JSON); measured RRF gap lists were below the base rate.
- **Gap and extra-rule candidates** (`openacquis.leftovers`; [docs/evaluation.md](docs/evaluation.md)):
  - Scope filters: EU final provisions and units addressed only to the Commission or ENISA (not the joint
    bodies of national representatives: Cooperation Group, CSIRTs network, EU-CyCLONe) on one side; national
    boilerplate on the
    other (definitions, penalty lists, transitional, repeal and entry-into-force clauses, amendments of other
    acts, transposition notes, footnotes, PDF-only annexes). Keyword lists in cs, sk, de and en.
  - A gap margin below the link threshold.
  - Structure: a lead-in or point of a linked list is not a gap; an extra-rule candidate needs a § with no
    link; only the main act can have extra rules.
  - Grouping per EU article and per national §.
  - `openacquis eval` reports the effect step by step against the table (`leftover_ablation`: nine rows,
    margins scaled to the method), with the article holding most gap candidates and the precision outside
    it, Wilson 95 % intervals, which set-aside units the table maps, and the margin chosen out of sample
    (`cross_validated_gap_margin`: article parity and leave one article out).
  - `find_leftovers(..., gaps=False, gap_note=...)`; the JSON `leftovers` block has `eu_linked_out_of_scope`
    and `gap_note`.
- **Review** (`openacquis.review`, [docs/review.md](docs/review.md)):
  - Record format in `data/reviews/<CELEX>_<cc>.jsonl` (suggestion ID, IDs, machine label and score, verdict
    confirm / reject / correct with corrected IDs, reviewer, date, note), with validation and merging into
    decisions (confirmed, rejected, corrected, disputed).
  - `openacquis review validate|add|stats`; `--author LOGIN` also requires every reviewer to be that user.
    Validation is strict (exact matches, no trailing newlines, finite scores, integer schema 1, national act
    IDs in `A-Za-z0-9_./-`), and error messages never repeat submitted text.
  - GitHub issue form `.github/ISSUE_TEMPLATE/review.yml`; only its "Review record" section is read.
  - Workflow `.github/workflows/reviews.yml`: `python -m openacquis.review check-issue FILE AUTHOR` validates
    review issues, requires each record's reviewer to be the issue author, and the result is commented as
    escaped code. Two least-privilege jobs (read-only `validate`, `issues: write` `comment` without checkout).
    It never commits; maintainers add records in a pull request.
- **Dataset** (`openacquis.dataset`, `openacquis dataset build`, [docs/dataset.md](docs/dataset.md)): `links.csv`
  (formula-like cells prefixed with `'`), `links.jsonl`, `reviews.jsonl`, `datapackage.json` (Data Package
  profile, CC-BY-4.0, EUR-Lex and national sources) and a README.
  Official correlation-table links (NIS2 × CZ: 568) plus reviewed links (none yet).
- **Website:**
  - `alignment.html` for NIS2 × SK, AT and DE next to CZ (rows without an official table where there is none;
    gap and extra-rule candidate sections).
  - `matrix.html?celex=32022L2555`: article × country matrix with English headings. A cell shows the top
    suggested §, the table's §§ (CZ), linked provisions and gap candidates, and links into the alignment pages.
  - `review.html?id=32022L2555_<cc>`: the review queue, a prefilled GitHub issue per verdict, a JSON-lines
    batch download.
  - Dataset downloads under `site/data/dataset/`.
  - Pages link only to data that was built (`link_built_pages`; `dataset` flag, nullable `review_href` and
    `matrix_href`) and say "Not part of this build" otherwise; `lang` on every EU and national text; no
    sideways scroll at 375 px; the review page checks records like the CLI, caps prefilled issue links at
    7,500 characters and announces only a short status line to screen readers.
  - Everything is stdlib-built (`hybrid-lex`), with committed fallbacks in `site/static-data/` (alignment,
    review data and headings per country).
- Optional extra `tls` (certifi), for RIS's certificate chain on Pythons whose OpenSSL bundle lacks the root.
- Docs: `docs/review.md`, `docs/dataset.md`, `data/reviews/README.md`. Updated: data-sources, provisions
  (SK, AT, DE kinds), website, how-it-works, evaluation, docs index, CONTRIBUTING (how to review).

### Results
- **Matching (NIS2 × CZ, 197 gold provisions).** `hybrid-lex` (stdlib), cross-validated: R@1 about 0.42,
  MRR 0.49–0.50 (lexical: 0.350 / 0.445). `hybrid` with e5: R@5 0.635, MRR 0.505 (lexical R@5 0.563).
- **Gap candidates against the table** (`hybrid-lex`, threshold 0.17, margin 0.01): 16 candidates, 13 not
  listed in the table (81 %; 95 % interval 57–93 %). With the margin chosen out of sample: 16 of 25 (64 %,
  split by article parity) or 16 of 21 (76 %, leave one article out).
  - v0.3: 118 candidates at 44 %.
  - Base rate: 28 % of EU units are not in the table.
  - 12 of the 16 are Art. 32(2) and 32(7); outside Art. 32, 1 of 4 is not in the table (25 %).
- **Extra-rule candidates** (`hybrid-lex`): 33 units in 11 §§, 27 not cited in the table (82 %; base rate
  50 %; v0.3: 195 units at 75 %).
- **SK, AT, DE (no gold set):**

  | | SK | AT | DE |
  |---|---:|---:|---:|
  | EU units with a link (of 473) | 454 | 463 | 448 |
  | Gap candidates | 5 | 1 | 6 |
  | Extra-rule candidates (§§) | 34 (7) | 13 (7) | 59 (12) |

  The thresholds are tuned on Czech. Austria's act reproduces much of NIS2's wording, so it scores high.

### Changed
- `openacquis align` and `eval`: the default embedder is now `auto` (`hybrid` with the embeddings extra,
  `hybrid-lex` without). Pass `--embedder lexical` for the v0.3 behaviour.
- Thresholds: `hybrid` 0.17 (stdlib channels) and 0.14 (with e5).
- `AlignmentResult.gap_candidates` is the filtered list; the v0.3 list is `AlignmentResult.unlinked`.
- `extra_rule_candidates` entries have `section_id`, `margin` and `best_eu_id`.
- Extra-rule candidates by default come from the first (main) act only.
- JSON export schema 2: `gap_candidates` are objects with evidence; new `leftovers` block.
- The `coverage` report adds `unlinked_eu_units`.
- Matching: the article / paragraph / skip kinds cover the Slovak, Austrian and German units (§, Artikel,
  Anlage, odsek, Absatz; structural units and footnotes skipped).
- `path_label` prints each country's citation style and the EU text in cs, sk and de.
- `cli.main` takes `slovlex=`, `ris=` and `gii=` clients for tests. `LAW_ADAPTERS` comes from the registry.
- Website alignment data schema 2 (`has_official`, gap and extra-rule sections, section labels). The CZ page
  now uses `hybrid-lex`.
- Version 0.4.0; User-Agent `OpenAcquis/0.4`.

### Known limitations
- **Gold sets.** Only Czechia has an official table. SK, AT and DE suggestions and candidates are unchecked
  until reviewed, and thresholds and margins are tuned on Czech.
- **Small samples.** The gap-candidate precision rests on 16 items, 12 of them in Art. 32; the margin and
  threshold were tuned on the same set.
- **Gold-plating.** Extra-rule candidates are mostly national procedure and organisation; telling real
  gold-plating apart needs the judge or a reviewer.
- **Judges.** The LLM judge has still not been run. The rule judge is not used for leftovers (it lowered
  link precision in v0.3).
- **Germany.** Only the federal BSIG; Land law and the laws the transposition act amends are not covered.
- **Austria.** NISG 2018 and NISV are repealed; fetching them needs a version date.
- **Reviews.** No reviews yet. The workflow validates but does not merge.

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
