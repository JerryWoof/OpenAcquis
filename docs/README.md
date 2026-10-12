# Documentation

| Document | What it covers |
|---|---|
| [how-it-works.md](how-it-works.md) | The pipeline end to end: notified measures, texts, provisions, matching, judging, evaluation, review, outputs |
| [evaluation.md](evaluation.md) | NIS2 × Czechia scored against the official correlation table: recall@k, precision / recall / F1, coverage, error analysis; v0.4 hybrid matching, gap and extra-rule candidates, the four-country runs; **held-out results on six more official tables (new in 0.5)**; the LLM judge run on NIS2 × CZ (unreleased) |
| [review.md](review.md) | **New in 0.4.** Reviewing machine suggestions: the review page, the record format, validation, GitHub issue form and workflow, how reviews are merged |
| [dataset.md](dataset.md) | **New in 0.4.** The open CC-BY-4.0 dataset: files, fields, sources, licence and attribution |
| [judge.md](judge.md) | The pair judge: labels, evidence checks, prompts per country, caching, prices; the resumable batch runner and its dispatch-only workflow (0.5); running it in Claude Code without an API key (`scripts/judge_session.py`); Claude, rule-based and fake judges |
| [provisions.md](provisions.md) | The `Document` / `Provision` data model and the stable provision ID scheme, including the Slovak, Austrian and German kinds (0.4) |
| [data-sources.md](data-sources.md) | Endpoints actually used (CELLAR, EUR-Lex, e-Sbírka, Slov-Lex, RIS, gesetze-im-internet.de, NeuRIS, psp.cz), their quirks, and which notified NIS2 measures each adapter can fetch |
| [website.md](website.md) | The GitHub Pages site: 12 directives × 4 countries from the registry, matrices, review pages, official columns, data & API page, change feed; how it is built and deployed |
| [api.md](api.md) | **New in 0.5.** The static JSON API (`api/v1/`), schemas, OpenAPI, releases, Zenodo |
| [alerts.md](alerts.md) | **New in 0.5.** Change alerts: what is watched, the weekly workflow, issues and the Atom feed |
| [../data/directives.json](../data/directives.json) | **New in 0.5.** The directive registry: main act per directive and country (`openacquis directives`) |
| [../data/eval/README.md](../data/eval/README.md) | The Czech evaluation sets (NIS2, CER, Accessibility Act, Whistleblower): source tables, licence, method, counts, known issues |
| [../data/eval/README_sk.md](../data/eval/README_sk.md) | **New in 0.5.** The Slovak evaluation sets (NIS2, CER, Pay Transparency) |
| [../data/reviews/README.md](../data/reviews/README.md) | Where review records live |

Elsewhere: [README](../README.md) (quickstart), [ROADMAP](../ROADMAP.md), [CHANGELOG](../CHANGELOG.md),
[CONTRIBUTING](../CONTRIBUTING.md) (including how to write a country adapter).
