# Documentation

| Document | What it covers |
|---|---|
| [how-it-works.md](how-it-works.md) | The pipeline end to end: notified measures, texts, provisions, matching, judging, evaluation, review, outputs |
| [evaluation.md](evaluation.md) | NIS2 × Czechia scored against the official correlation table: recall@k, precision / recall / F1, coverage, error analysis; v0.4 hybrid matching, **gap and extra-rule candidates** (new in 0.4) and the four-country runs |
| [review.md](review.md) | **New in 0.4.** Reviewing machine suggestions: the review page, the record format, validation, GitHub issue form and workflow, how reviews are merged |
| [dataset.md](dataset.md) | **New in 0.4.** The open CC-BY-4.0 dataset: files, fields, sources, licence and attribution |
| [judge.md](judge.md) | The pair judge: labels, evidence checks, prompt, caching, cost estimate; Claude, rule-based and fake judges |
| [provisions.md](provisions.md) | The `Document` / `Provision` data model and the stable provision ID scheme, including the Slovak, Austrian and German kinds (0.4) |
| [data-sources.md](data-sources.md) | Endpoints actually used (CELLAR, EUR-Lex, e-Sbírka, Slov-Lex, RIS, gesetze-im-internet.de, NeuRIS, psp.cz), their quirks, and which notified NIS2 measures each adapter can fetch |
| [website.md](website.md) | The GitHub Pages site: alignment pages for four countries, the article × country matrix, the review page, dataset downloads; how it is built and deployed |
| [../data/eval/README.md](../data/eval/README.md) | The evaluation set: source table, licence, method, counts, known issues |
| [../data/reviews/README.md](../data/reviews/README.md) | Where review records live |

Elsewhere: [README](../README.md) (quickstart), [ROADMAP](../ROADMAP.md), [CHANGELOG](../CHANGELOG.md),
[CONTRIBUTING](../CONTRIBUTING.md) (including how to write a country adapter).
