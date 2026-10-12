# Evaluation data

Gold links between EU directive provisions and national provisions, taken from official
correlation tables. They are what a government *asserted* when it sent a bill to parliament:
reference data for scoring automatic matching, not findings about whether a provision is
correctly transposed.

## `nis2_cz.jsonl`: NIS2 (32022L2555) × Czechia, Act No. 264/2025 Sb.

| | |
|---|---|
| Source | *Rozdílová tabulka návrhu právního předpisu s předpisy EU*, attachment "Kompatibilita s právem EU" to the government bill of the Cybersecurity Act (*Vládní návrh zákona o kybernetické bezpečnosti*), Chamber of Deputies print (sněmovní tisk) **759/0**, 9th term, sent to deputies 25 July 2024 |
| Bill history | https://www.psp.cz/sqw/historie.sqw?o=9&t=759 |
| File used | `t0759c0.doc` (Word 97, 1.5 MB, 198 pages): https://www.psp.cz/sqw/text/orig2.sqw?idd=244067 (a PDF of the same table is at `idd=244068`) |
| Retrieved | 2026-10-12 |
| Checked against | NIS2 in Czech from CELLAR (509 provisions) and Act 264/2025 Sb. from e-Sbírka, version in force from 2025-11-01 (736 provisions) |
| Regenerate | `python scripts/build_evalset.py` (stdlib only; `--table FILE` for a local copy, `--no-check` to skip the texts) |

**Licence.** The table is part of a government bill, an official work (*úřední dílo*) that
Czech copyright law does not protect (§ 3 of Act No. 121/2000 Sb.). It quotes NIS2: © European
Union, https://eur-lex.europa.eu, reused under Commission Decision 2011/833/EU. The derived
dataset is published under CC-BY-4.0, like other OpenAcquis data.

### How it was built

1. The `.doc` is read with the standard library (`openacquis.evalset.doc_text`: the OLE
   container, the Word piece table and its cell marks) and split into rows of five cells:
   bill provision, bill text, CELEX, EU provision, EU text. An empty bill cell continues the
   bill provision above it (a merged cell). Rows for other EU acts quoted in the table
   (2019/881, 2021/887, 2021/696, 2023/588, decision 1104/2011) are left out.
2. Each printed reference becomes provision IDs (`parse_eu_ref`, `parse_cz_ref`). Lists and
   ranges expand: `§ 4 odst. 1 písm. a) bod 1 až 11, 13 a 15 a písm. b)` gives 14 IDs,
   `Čl. 32 odst. 1 a 4 písm. e)` gives `art32.par1` and `art32.par4.pt_e` (a lower level
   after a list belongs to the last item). Sentence and subparagraph qualifiers
   (`věta první`, `druhý pododstavec`) cannot be expressed in IDs; the link points to the
   paragraph and the qualifier is kept in `note`. One link is written for every
   (EU ID, Czech ID) pair of a row.
3. EU IDs are looked up in the parsed directive. The table cites the definitions in Art. 6
   as `Čl. 6 odst. 5`; the directive numbers them as points, so those IDs are mapped to
   `art6.pt5` (`eu_check: "fallback"`, noted).
4. Czech IDs: the table quotes the **bill**, whose numbering can differ from the act as
   published. For every Czech ID the bill text quoted in the table is compared with the
   act's text at that ID (`national_similarity`, share of words found in order). If it does
   not match, the provision of the same kind whose text matches best is used instead and the
   link is marked `renumbered`; the bill's ID stays in `bill_national_id`.

### Fields

| Field | Meaning |
|---|---|
| `eu_id` | NIS2 provision, Czech language version: `eu:32022L2555:cs:art23.par4.pt_a` |
| `national_id` | Provision of Act 264/2025 Sb. as published: `cz:264/2025:par16.odst1` |
| `status` | `listed`: the pair is listed in the table. This table template has **no compatibility column** (no "plně slučitelné" etc.), so no compatibility is claimed |
| `raw_eu_ref`, `raw_national_ref` | References exactly as printed in the table |
| `source_url` | The table file on psp.cz |
| `note` | Qualifiers, ID fallbacks, renumbering |
| `celex`, `row` | EU act, and the data row in the table: 0-based index among all 521 data rows of the table, rows for other EU acts included (so values run to 520; the NIS2 links come from 479 of them) |
| `bill_national_id` | The Czech ID in the bill's numbering |
| `eu_check` | `exact`, `fallback` (see step 3) or `unresolved` |
| `eu_similarity` | 0–1: how well the table's EU text supports `eu_id` |
| `national_check` | `same_number` (text confirms the same ID in the act), `renumbered`, `unverified` (ID exists, text does not confirm it) or `unresolved` |
| `national_similarity` | 0–1: how well the quoted bill text supports `national_id` |
| `eu_text`, `national_text` | First 500 characters of the table's text cells (evidence; the bill text, not the act) |

### Counts (build of 2026-10-12)

| | |
|---|---|
| Table rows for NIS2 | 479 (527 table rows in all) |
| Gold links | 569 (568 distinct pairs; one pair is listed in two rows) |
| Distinct EU IDs | 197, in 33 articles |
| Distinct Czech IDs | 243 |
| References that did not parse | 0 |
| EU IDs found in the parsed directive | 197 / 197 (100 %): 172 as printed, 25 via the Art. 6 point fallback (87.3 % literally) |
| Czech IDs found in the published act | 243 / 243 (100 %): 237 confirmed by text at the same number, 6 renumbered |
| Bill IDs that exist literally in the act | 241 / 243 |

The full numbers, with lists of unresolved IDs, are in `nis2_cz_summary.json`.

Renumbered (bill -> act, confirmed by text): `§ 42 odst. 3 písm. t)–x)` -> `písm. s)–w)` (the
bill had one more letter before them), and `§ 5 odst. 1 písm. d)` -> `§ 5 písm. d)` (§ 5 has
no paragraphs).

### Known issues

- **Bill, not act.** The table describes the bill as submitted in July 2024. Amendments adopted
  later (prints 759/7–759/9) can change content without changing numbers; such changes are not
  visible here. Text checks only confirm that the act's provision is the one quoted.
- **Coarse checks for lists.** When a row cites several provisions, the bill text usually
  quotes all of them, so every listed ID passes the text check; a wrong item in a list would
  not be caught.
- **Errors in the table itself.** Some cells quote another letter than the reference says
  (`§ 2 odst. 1 písm. c)` quotes `a) aktivem …`, which is `písm. c)` in the act;
  `§ 2 odst. 3 písm. g)` quotes text labelled `a)`). The references were kept; the text check
  agrees with them. Two links have low EU support (`eu_similarity` < 0.5): `Čl. 28 odst. 3 a 6`
  (the cell quotes only paragraph 3) and `Čl. 32 odst. 5 písm. a)` (the cell quotes another part).
- **Sentence-level references** (44 links) point to the whole paragraph.
- **Coverage.** Only the Cybersecurity Act is covered. The companion bill (print 760, changes
  to other laws) and the implementing decrees transpose further parts of NIS2 and have their own
  tables. Articles addressed to the Commission, ENISA or the Cooperation Group are mostly absent,
  as expected.
- **No compatibility status.** The 2024 table template only pairs provisions; `status` is
  always `listed`. Older Czech tables have a compatibility column; the parser reads it
  (`fully_compatible`, `partially_compatible`, …) when present.
- **Stdlib `.doc` reader.** It handles the tables seen here (cell marks, fields, merged cells)
  but not encrypted files or text stored in the mini stream. Rows are recognised by their
  five-cell shape and a CELEX in the third cell.
