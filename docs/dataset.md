# The open dataset

OpenAcquis publishes provision-level transposition links as an open dataset under
**CC-BY-4.0**. A row says that one source paired one EU provision with one national provision. A row
does not say that the national provision transposes the EU provision correctly or completely.

```bash
openacquis dataset build                 # writes dataset/ (gitignored)
openacquis dataset build -o /tmp/ds --reviews data/reviews
```

The website build writes the same files to `site/data/dataset/`. They are downloadable from the home
page:

- [links.csv](https://jerrywoof.github.io/OpenAcquis/data/dataset/links.csv)
- [links.jsonl](https://jerrywoof.github.io/OpenAcquis/data/dataset/links.jsonl)
- [datapackage.json](https://jerrywoof.github.io/OpenAcquis/data/dataset/datapackage.json)

Releases will attach the same files.

## Files

| File | Content |
|---|---|
| `links.csv`, `links.jsonl` | One row per (EU provision, national provision, source). In `links.csv` a text cell starting with `=`, `+`, `-`, `@`, a tab or a carriage return gets a leading `'`, so spreadsheets do not run it as a formula; `links.jsonl` keeps the raw values |
| `reviews.jsonl` | Every human review record, including rejections and disputed ones ([review.md](review.md)) |
| `datapackage.json` | [Frictionless Data Package](https://specs.frictionlessdata.io/data-package/) (`profile: "data-package"`): field descriptions and types, licence, sources |
| `README.md` | Counts, citation, attribution |

## Sources of rows

| `source` | `status` | What it means | Today |
|---|---|---|---|
| `official_correlation_table` | `listed` | A government listed the pair in the correlation table attached to its bill. The Czech template has no compatibility column, so no compatibility is claimed | Czechia, 4 tables: NIS2 568 links (569 in the table, one pair listed twice), CER 229, Accessibility Act 146, Whistleblower 683 (685 in the table; [data/eval/README.md](../data/eval/README.md)) |
| `official_correlation_table` | `fully_compatible` / `partially_compatible` / `incompatible` | The same, from a Slovak *tabuľka zhody*, which has a compatibility column (*zhoda* Ú / Č / Ž); the value is what the government stated about its bill. Rows where the government says nothing transposes the EU provision (`n.a.`) are not links and are left out | Slovakia, 3 tables: NIS2 469 (470 in the table), CER 409 (414), Pay Transparency 232 (232; [data/eval/README_sk.md](../data/eval/README_sk.md)) |
| `human_review` | `confirmed` / `corrected` | A reviewer confirmed a machine suggestion, or corrected it with other provisions (merged as in [review.md](review.md)) | None yet |

In all, 2,736 links from the 7 tables (each distinct pair once).

`relation` says what kind of pairing a row is. `transposition` (every official-table row, and reviews of
notified acts): the national provision is cited as transposing the EU provision. `existing_law`: a human
review on a *baseline* pair (registry status `baseline`: the act aligned was not notified for the directive,
e.g. Pay Transparency × AT and DE). The reviewer confirmed that existing national law covers the EU
provision, which is not a notified transposition.

Unreviewed machine suggestions are **not** part of the dataset; they are on the website and in
`openacquis align` output, labelled as candidates.

## Fields (`links.csv`)

| Field | Example | Meaning |
|---|---|---|
| `link_id` | `3f2a…` | Stable hash of directive, EU provision, national provision and source |
| `directive` | `32022L2555` | CELEX number |
| `country` | `cz` | ISO 3166-1 alpha-2 |
| `eu_provision` | `art23.par4.pt_a` | EU provision, language-independent |
| `eu_id` | `eu:32022L2555:cs:art23.par4.pt_a` | The same in the language version used |
| `national_act` | `264/2025` | Act as the adapter names it (Gesetzesnummer for Austria, portal slug for Germany) |
| `national_id` | `cz:264/2025:par16.odst1` | National provision ([provisions.md](provisions.md)) |
| `source`, `status` | see above | |
| `relation` | `transposition` | `transposition`, or `existing_law` for reviews of an act not notified for the directive (see above) |
| `source_title`, `source_url` | table title and psp.cz link; or "OpenAcquis review" and the review file | Where to check it |
| `reviewers`, `review_date` | `octocat;alice`, `2026-10-12` | Human review only |
| `machine_score`, `machine_method` | `0.58`, `hybrid:…` | The suggestion that was reviewed |
| `raw_eu_ref`, `raw_national_ref` | `Čl. 23 odst. 4 písm. a)`, `§ 16 odst. 1` | As printed in the official table |
| `note` | `věta první` | Qualifiers, renumbering, reviewer notes |

## Licence and attribution

- **Dataset:** CC-BY-4.0. Cite as *OpenAcquis contributors, OpenAcquis transposition links, version
  0.5.0, https://github.com/JerryWoof/OpenAcquis*.
- **EU texts and IDs:** © European Union, https://eur-lex.europa.eu. Reused under Commission
  Decision 2011/833/EU.
- **National texts:**
  - e-Sbírka (CZ), Slov-Lex (SK) and gesetze-im-internet.de (DE): official works.
  - RIS (AT): open government data under CC BY 4.0.
- **Czech correlation table:** part of a government bill, an official work (*úřední dílo*, § 3 of
  Act No. 121/2000 Sb.).

## Limits

- Seven official tables so far (four Czech, three Slovak), none for Austria or Germany. They describe
  government bills, renumbered to the acts as published ([data/eval/README.md](../data/eval/README.md),
  [data/eval/README_sk.md](../data/eval/README_sk.md)).
- Links point to whole provisions. Sentence and subparagraph qualifiers are in `note`.
- Reviews are individual readings. Disagreements are kept as `disputed` and left out of the links.
