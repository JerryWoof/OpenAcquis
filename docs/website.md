# Website

**https://jerrywoof.github.io/OpenAcquis/** (published by the Pages workflow after the first deploy; see *Deployment* below)

A static site on GitHub Pages. Today it shows **act-level** data for ten directives: for each one, the
national measures each member state has notified as transposing it, read from CELLAR. Provision-level
matching (the article × country matrix) comes later; see the [roadmap](../ROADMAP.md).

| Page | What it shows |
|---|---|
| `index.html` | Status, one card per directive (measures, member states with at least one), how it works, disclaimer, data build time |
| `directive.html?celex=32022L2555` | Title, deadline, links to the EUR-Lex text and NIM page; a grid of the 27 member states coloured by number of measures, with a ranked list next to it that also serves as a text version for screen readers; a table of measures you can filter by country and by text, and sort. Filters are kept in the URL |

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
        ▼
site/*.html + assets/app.js   plain HTML/CSS/JS, no build step, no external CDNs; reads the JSON
```

- Failed requests are retried 3 times with growing waits, with a 2-second pause between directives.
- If one directive fails, the build carries on. The error is stored in that directive's JSON and the site shows
  a "data unavailable" card. The script exits with an error only if **every** directive fails, so a broken
  endpoint never deploys an empty site.
- `site/data/` is generated and gitignored.

Options: `--offline` (use the small saved answers in `scripts/sample_data/`, no network; the site then shows a
"Sample data" banner), `--only CELEX …`, `--cache DIR` (keep SPARQL answers for 24 h), `--retries`, `--pause`, `--out`.

## Preview locally

```bash
pip install -e .
python scripts/build_site.py              # live data (about 30 s), or add --offline
python -m http.server 8000 -d site
# open http://localhost:8000/ and http://localhost:8000/directive.html?celex=32022L2555
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
