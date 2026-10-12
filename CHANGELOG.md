# Changelog

All notable changes to OpenAcquis. Versions follow [Semantic Versioning](https://semver.org/); while the
version is 0.x, the command-line options and data formats can still change.

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
