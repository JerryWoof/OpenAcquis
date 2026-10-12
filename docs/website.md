# Website

**https://jerrywoof.github.io/OpenAcquis/** (published by the Pages workflow after the first deploy; see *Deployment* below)

A static site on GitHub Pages. It shows **act-level** data for ten directives: for each one, the national
measures each member state has notified as transposing it, read from CELLAR. It also has a first
**provision-level** page, NIS2 × Czechia. The full article × country matrix comes later; see the
[roadmap](../ROADMAP.md).

| Page | What it shows |
|---|---|
| `index.html` | Status, one card per directive (measures, member states with at least one), how it works, disclaimer, data build time |
| `directive.html?celex=32022L2555` | Title, deadline, links to the EUR-Lex text and NIM page; a grid of the 27 member states coloured by number of measures, with a ranked list next to it that also serves as a text version for screen readers; a table of measures you can filter by country and by text, and sort. Filters are kept in the URL. Links to provision-level pages where they exist |
| `alignment.html?id=32022L2555_cz` | NIS2 × Czechia, article by article. One row per NIS2 paragraph shows the Czech provisions listed in the government's **official correlation table** (rozdílová tabulka, tisk 759/0) and the top **machine suggestion** (lexical matching, unreviewed), each with a different label. The row also says whether the suggestion matches the table, is in the same §, or differs; a paragraph for which the table cites only the whole article (e.g. Čl. 35) is marked as listed at article level and not compared. "Texts" opens the NIS2 paragraph, the official provisions and the top 3 suggestions side by side, with links to EUR-Lex and e-Sbírka. You can filter by article, by agreement and by text; filters are kept in the URL |

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
scripts/build_site.py ──► CELLAR (NIS2, Czech) + e-Sbírka (264/2025) ──► openacquis.align (lexical)
        │                  + data/eval/nis2_cz.jsonl (official correlation table)
        ▼
site/data/align/32022L2555_cz.json   rows per NIS2 paragraph, official + machine, texts
        (if the texts cannot be fetched: copied from the committed site/static-data/32022L2555_cz.json)
        │
        ▼
site/*.html + assets/app.js   plain HTML/CSS/JS, no build step, no external CDNs; reads the JSON
```

- Failed requests are retried 3 times with growing waits, with a 2-second pause between directives.
- If one directive fails, the build carries on. The error is stored in that directive's JSON and the site shows
  a "data unavailable" card. The script exits with an error only if **every** directive fails, so a broken
  endpoint never deploys an empty site.
- `site/data/` is generated and gitignored.
- **Alignment pages** use only the stdlib lexical embedder, so the Pages workflow needs no heavy
  dependencies. They fetch NIS2 in Czech from CELLAR and Act 264/2025 Sb. from e-Sbírka, pinned to the
  version of 2025-11-01, which the correlation table was checked against. If that fails (network, API
  change), the builder copies `site/static-data/<id>.json`, which is committed, and the page says it shows
  a precomputed copy. After changing the matcher, refresh the copy and commit it with
  `python scripts/build_site.py --write-static`. `--no-alignment` skips these pages.
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
- `--no-alignment`, `--write-static`

## Preview locally

```bash
pip install -e .
python scripts/build_site.py              # live data (about 30 s), or add --offline
python -m http.server 8000 -d site
# open http://localhost:8000/, http://localhost:8000/directive.html?celex=32022L2555
# and http://localhost:8000/alignment.html?id=32022L2555_cz
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
