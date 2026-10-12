# Website

**https://jerrywoof.github.io/OpenAcquis/** (published by the Pages workflow after the first deploy; see *Deployment* below)

A static site on GitHub Pages. It shows **act-level** data for ten directives: for each one, the national
measures each member state has notified as transposing it, read from CELLAR. For **NIS2** it goes down to the
provision in four countries (Czechia, Slovakia, Austria, Germany), with an article × country matrix, a review
page and the open dataset. See the [roadmap](../ROADMAP.md) for what comes next.

| Page | What it shows |
|---|---|
| `index.html` | Status, one card per directive (measures, member states with at least one, links to the provision-level pages and the matrix), how it works, dataset downloads, disclaimer, data build time |
| `directive.html?celex=32022L2555` | Title, deadline, links to the EUR-Lex text and NIM page; a grid of the 27 member states coloured by number of measures, with a ranked list next to it that also serves as a text version for screen readers; a table of measures you can filter by country and by text, and sort. Filters are kept in the URL. Links to provision-level pages where they exist |
| `alignment.html?id=32022L2555_cz` (also `_sk`, `_at`, `_de`) | One directive × one country, article by article. One row per paragraph shows the top **machine suggestion** (`hybrid-lex` matching, unreviewed) and, where a government published one (Czechia: tisk 759/0), the provisions its **official correlation table** lists, each with a different label; with a table the row also says whether the suggestion matches it, is in the same §, or differs. Rows show their **gap candidates**; below the table, gap candidates per article and **extra-rule candidates** per national § with their texts. "Texts" opens the EU paragraph, the official provisions and the top 3 suggestions side by side, with links to EUR-Lex and the national portal. Filters (article, agreement, gap candidates, text) are kept in the URL; `&art=art23` (language-neutral) or a full article ID selects one article |
| `matrix.html?celex=32022L2555` | **Article × country matrix**: one row per article and annex (English headings), one column per country. A cell shows the national § the machine suggests most often for the article's paragraphs (with its score and the other §§ suggested), the §§ the official table lists (Czechia), how many of the article's provisions have a suggestion, and gap candidates. Each cell links to that article on the country's alignment page. The top suggestion carries a "machine" label and its score ("score 0.53"). The article column and corner header stay in place when the table scrolls sideways on a phone. Filter by text or to articles with gap candidates |
| `review.html?id=32022L2555_cz` | The **review queue** ([review.md](review.md)): unreviewed suggestions one by one (links, gap candidates, extra-rule candidates; filter by kind and article), texts side by side, a verdict (confirm / reject / correct with IDs), and either a prefilled GitHub issue or a batch download as JSON lines. Alternative candidates are styled as machine data, not as the official table. The page checks records as `openacquis review validate` does (at most 20 corrected IDs, of this directive or country, dotted paths only) and offers no issue link longer than 7,500 characters (shorten the note or use the batch download). Screen readers hear only a short status line per suggestion. The reviewer's handle and batch stay in the browser's local storage only; the handle must be the account that opens the issue |
| `data/dataset/` | The open dataset ([dataset.md](dataset.md)): `links.csv`, `links.jsonl`, `reviews.jsonl`, `datapackage.json` |

Directives included (`DIRECTIVES` in `scripts/build_site.py`): NIS2 (32022L2555), European Accessibility Act
(32019L0882), Pay Transparency (32023L0970), CSRD (32022L2464), Whistleblower (32019L1937), Critical Entities
Resilience (32022L2557), Copyright in the Digital Single Market (32019L0790), Work-Life Balance (32019L1158),
Adequate Minimum Wages (32022L2041), European Electronic Communications Code (32018L1972).

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
  review, matrix and dataset pages that are not in this build (including the "NIS2 matrix" nav link)
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
notified measures) and on manual trigger. It installs the package on Python 3.12, runs the offline tests (`pytest -m "not live"`; a failure stops the deploy), runs `build_site.py`, and
deploys `site/` with `actions/upload-pages-artifact` and `actions/deploy-pages`. Only one deployment runs at
a time.

One-time setup: in the repository, **Settings → Pages → Source** must be set to **GitHub Actions**. The
workflow cannot change this itself.

## Adding a directive

Add an `Entry(celex, short, topic)` to `DIRECTIVES` in `scripts/build_site.py`, check the CELEX number with
`openacquis summary <CELEX>`, and run the offline tests (`tests/test_build_site.py` checks the list).
