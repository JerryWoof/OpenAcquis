# Website

**https://jerrywoof.github.io/OpenAcquis/** (published by the Pages workflow after the first deploy; see *Deployment* below)

A static site on GitHub Pages (v0.5, the v1.0 preview). Every directive of the registry
([`data/directives.json`](../data/directives.json), 12 directives) is shown act level (the national measures each
member state notified, from CELLAR) **and** provision level in Czechia, Slovakia, Austria and Germany: 46
directive × country alignment pages with review queues, 12 article × country matrices, the official correlation
table next to the machine suggestions for 7 pairs (CZ: NIS2, CER, Accessibility Act, Whistleblower; SK: NIS2,
CER, Pay Transparency), a data and API page and an Atom change feed. Two more directives (CSRD, Work-Life
Balance) are act level only. Countries with nothing to align (Austria has no statutory minimum wage; Czechia
has no specific Women on Boards act) are shown as such. Where the act aligned was not notified for the directive
(Pay Transparency in AT and DE, registry status `baseline`), it is compared as existing law: the pages say "<act>
was not notified for this directive" and how many measures the country did notify (Austria: Land laws, none of
them the act aligned; Germany: none), mark it "existing law", and ask reviewers whether the provision *covers* the
EU provision rather than *implements* it.

| Page | What it shows |
|---|---|
| `index.html` | Status, one card per directive (measures, member states with at least one, links to the provision-level pages and the matrix), how it works, dataset downloads, disclaimer, data build time |
| `directive.html?celex=32022L2555` | Title, deadline, links to the EUR-Lex text and NIM page; a grid of the 27 member states coloured by number of measures, with a ranked list next to it that also serves as a text version for screen readers; a table of measures you can filter by country and by text, and sort. Filters are kept in the URL. Links to provision-level pages where they exist |
| `alignment.html?id=32022L2555_cz` (also `_sk`, `_at`, `_de`) | One directive × one country, article by article. One row per paragraph shows the top **machine suggestion** (`hybrid-lex` matching, unreviewed) and, where a government published one (Czechia: tisk 759/0), the provisions its **official correlation table** lists, each with a different label; with a table the row also says whether the suggestion matches it, is in the same §, or differs, or that the table cites only acts the page does not search (counted apart, not as a difference). Rows show their **gap candidates**; below the table, gap candidates per article and **extra-rule candidates** per national § with their texts (none for general codes such as a Labour Code or the AktG, for acts mostly broader than the directive, and for existing law not notified for it; the page says why). "Texts" opens the EU paragraph, the official provisions and the top 3 suggestions side by side, with links to EUR-Lex and the national portal. Filters (article, agreement, gap candidates, text) are kept in the URL; `&art=art23` (language-neutral) or a full article ID selects one article |
| `matrix.html?celex=32022L2555` | **Article × country matrix**: one row per article and annex (English headings), one column per country. A cell shows the national § the machine suggests most often for the article's paragraphs (with its score and the other §§ suggested), the §§ the official table lists (Czechia), how many of the article's provisions have a suggestion, and gap candidates. Each cell links to that article on the country's alignment page. The top suggestion carries a "machine" label and its score ("score 0.53"). The article column and corner header stay in place when the table scrolls sideways on a phone. Filter by text or to articles with gap candidates |
| `review.html?id=32022L2555_cz` | The **review queue** ([review.md](review.md)): unreviewed suggestions one by one (links, gap candidates, extra-rule candidates; filter by kind and article), texts side by side, a verdict (confirm / reject / correct with IDs), and either a prefilled GitHub issue or a batch download as JSON lines. Alternative candidates are styled as machine data, not as the official table. The page checks records as `openacquis review validate` does (at most 20 corrected IDs, of this directive or country, dotted paths only) and offers no issue link longer than 7,500 characters (shorten the note or use the batch download). Screen readers hear only a short status line per suggestion. The reviewer's handle and batch stay in the browser's local storage only; the handle must be the account that opens the issue |
| `data.html` | **New in 0.5.** Dataset downloads with links per directive × country (from `api/v1/index.json`), the static API (`api/v1/`, [api.md](api.md)), the change feed and the command line |
| `feed.xml` | **New in 0.5.** Atom feed of change alerts ([alerts.md](alerts.md)), rendered from `data/alerts/state.json` at build time |
| `api/v1/` | **New in 0.5.** The static JSON API built by `scripts/build_api.py` after `build_site.py` |
| `data/dataset/` | The open dataset ([dataset.md](dataset.md)): `links.csv`, `links.jsonl`, `reviews.jsonl`, `datapackage.json` |

Directives included: the registry, in its order (`DIRECTIVES` and `ALIGNMENTS` in `scripts/build_site.py` are
derived from it with `openacquis.directives.load_registry()`; official tables come from
`openacquis.goldsets`), then the act-level-only `ACT_LEVEL_ONLY` list.

### v0.5: many directives

- **Which pages are computed live.** By default (`--live-alignments seed`) only NIS2 is aligned live during the
  Pages build, as before; the other 42 pairs are copied from `site/static-data/`, which
  `python scripts/build_alignments.py` refreshes (resumable, polite: one request at a time per portal, cached
  under `$OPENACQUIS_CACHE`). `--live-alignments all` computes everything (minutes, and hundreds of RIS requests
  without a cache); `none` uses only the static copies. Static copies in the compact format 2 (no texts) are
  refused until the pages can fetch texts on demand.
- **Official columns** appear wherever a gold set exists for the pair; the copies built by
  `build_alignments.py` carry the table's citation. The pages search only the registry's main act(s), not every
  act a Slovak table cites, so official provisions in other acts are listed without a text and marked "in an act
  not searched here". A row whose official provisions are all in such acts has agreement `other_act` (Pay
  Transparency × SK: 17 rows) and counts neither as a match nor as a difference.
- **Registry wording.** Registry notes are shown in their public form: a `public_note` when the registry gives
  one, else the note without developer clauses (mapper statuses such as `not_found`, slugs), by `public_note()` in
  `build_site.py`. Secondary acts are cited by the registry's `act_labels` (UrhG, BFSGV, EWKFondsG). The status
  label "Main act (registry)" names the act aligned; baseline columns of the matrix carry an "existing law" pill
  and a hatched background.
- **Phones.** The provision table stacks its rows under 600 px, with "Main act", "Status" and "Pages" labels.
- **States per country.** `<CELEX>.json` and `index.json` carry `alignments` (with `status`: `ok` or
  `baseline`, the act label and the registry note) and `not_aligned` (status `none`); the matrix JSON has
  `not_aligned` and a `status` per column. Offline builds list every directive; those without a bundled
  CELLAR sample have `measures_available: false` and say so instead of showing zeros.
- **Size.** About 35 MB of page data (46 alignment and review files with texts) plus 32 MB of API; the Pages
  workflow deploys `site/` without `site/static-data/` (29 MB; about 66 MB deployed in all).

## How it is built

```
scripts/build_site.py ──► CELLAR SPARQL (directive metadata + national measures, per directive)
        │
        ▼
site/data/index.json          one entry per directive, counts for all 27 member states
site/data/<CELEX>.json        the same plus every measure
        │
scripts/build_site.py ──► CELLAR (NIS2 in cs, sk, de) + e-Sbírka / Slov-Lex / RIS / gesetze-im-internet.de
        │                  ──► openacquis.align (hybrid-lex, stdlib) + data/eval/nis2_cz.jsonl (official table, CZ)
        ▼
site/data/align/32022L2555_<cc>.json    rows per NIS2 paragraph, official + machine, gap and extra-rule candidates, texts
site/data/review/32022L2555_<cc>.json   every suggestion with its texts; reviewed ones marked from data/reviews/
site/data/matrix/32022L2555.json        article x country, derived from the four alignment files (+ English headings)
site/data/dataset/                      openacquis.dataset: official table links + reviewed links
site/data/index.json, <CELEX>.json      written last, linking only to the pages above that were built
        (if the texts cannot be fetched: copied from the committed site/static-data/32022L2555_<cc>.json,
         <cc>.review.json and 32022L2555.headings.json)
        │
        ▼
site/*.html + assets/app.js   plain HTML/CSS/JS, no build step, no external CDNs; reads the JSON
```

- Failed requests are retried 3 times with growing waits, with a 2-second pause between directives.
- If one directive fails, the build carries on. The error is stored in that directive's JSON and the site shows
  a "data unavailable" card. The script exits with an error only if **every** directive fails, so a broken
  endpoint never deploys an empty site.
- `site/data/` is generated and gitignored.
- **Alignment pages** use only the stdlib `hybrid-lex` embedder, so the Pages workflow needs no heavy
  dependencies. They fetch NIS2 in each act's language from CELLAR and the main transposing act from the
  national portal: 264/2025 Sb. (e-Sbírka, pinned to the version of 2025-11-01, which the correlation table
  was checked against), 69/2018 Z. z. (Slov-Lex), NISG 2026 (RIS) and the BSIG (gesetze-im-internet.de), the
  last three in their current version. If a country fails (network, API change), the builder copies its
  files from `site/static-data/`, which is committed, and the page says it shows a precomputed copy. After
  changing the matcher, refresh the copies and commit them with `python scripts/build_site.py --write-static`
  (about 15 s with a warm cache; Austria needs about 60 requests the first time). `--no-alignment` skips
  these pages, `--countries cz sk` limits them.
- **Review data** is rebuilt from the same alignment; the `reviewed` marks come from `data/reviews/` at build
  time, so a merged review hides its suggestion from the queue on the next deploy, even from a static copy.
- **The dataset** is built from `data/eval/` and `data/reviews/`; an invalid review file skips the dataset
  (logged) but never the site. `--no-dataset` skips it.
- **Links only to what was built.** `index.json` and the per-directive files are written after the
  alignment and dataset steps (`link_built_pages`). A directive lists an alignment page in `alignments`
  only if `align/<slug>.json` exists; `review_href` and `matrix_href` are `null` when their file is
  missing; a `dataset` boolean says whether the downloads exist. On every page, links to alignment,
  review, matrix and dataset pages that are not in this build
  become plain text, and an alignment, matrix or review URL with no data shows "Not part of this build"
  instead of an error.
- **Languages.** `align/*.json` has `national_language`, `review/*.json` has `eu_language` and
  `national_language`; the pages set `lang` on every EU and national text, label and heading, so screen
  readers read Czech, Slovak or German correctly.
- When the method gives no gap list (`hybrid-rrf`), `align/*.json` carries its `gap_note` and the page
  shows it in place of the gap list. `summary.eu_units_out_of_scope` counts the unlinked EU units the
  scope filter set aside; `summary.eu_units_linked_out_of_scope` counts linked units in the same
  categories.
- All data is inserted into the page with `textContent` or attributes, never `innerHTML`. Links are kept only
  when the browser parses them as `http:` or `https:` URLs, or as relative links without a scheme; anything
  else (`javascript:`, including variants with tabs, newlines or control characters inside the scheme) is
  dropped.

Options:

- `--offline`: use the small saved answers in `scripts/sample_data/` and the static alignment copy, with no
  network. The site then shows a "Sample data" banner.
- `--only CELEX …`
- `--cache DIR`: keep SPARQL answers for 24 h.
- `--retries`, `--pause`, `--out`
- `--no-alignment`, `--countries CC …`, `--write-static`, `--no-dataset`

## Preview locally

```bash
pip install -e .
python scripts/build_site.py              # live data (about 30 s), or add --offline
python -m http.server 8000 -d site
# open http://localhost:8000/, http://localhost:8000/directive.html?celex=32022L2555,
# http://localhost:8000/alignment.html?id=32022L2555_sk, http://localhost:8000/matrix.html?celex=32022L2555
# and http://localhost:8000/review.html?id=32022L2555_at
```

## Deployment

`.github/workflows/pages.yml` runs on every push to `main`, every Monday at 04:17 UTC (to pick up newly
notified measures) and on manual trigger (the alerts workflow also triggers it after a change). It installs the package on Python
3.12 without extras, runs the offline tests (`pytest -m "not live"`; a failure stops the deploy), runs
`build_site.py` (which also writes `site/feed.xml`), then `build_api.py --check` (a schema error stops the
deploy), and deploys `site/` without `site/static-data/` with `actions/upload-pages-artifact` and
`actions/deploy-pages`. Only one deployment runs at
a time.

One-time setup: in the repository, **Settings → Pages → Source** must be set to **GitHub Actions**. The
workflow cannot change this itself.

## Adding a directive

Add it to `data/directives.json` (CELEX, short name, topic, rank, deadlines and the main act per country; check
the CELEX with `openacquis summary <CELEX>` and the acts with `openacquis measures <CELEX> -c CZ --map`), run
`python scripts/build_alignments.py --only <CELEX>` and commit the new `site/static-data/` files, then fetch the
English headings for its matrix (`<CELEX>.headings.json`, written by `build_site.py --write-static`). Run the
offline tests: `tests/test_align.py` checks every static copy and that each matrix has its headings. For an
act-level-only directive, add an `Entry` to `ACT_LEVEL_ONLY` in `scripts/build_site.py`.
