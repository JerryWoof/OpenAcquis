# Evaluation data

Gold links between EU directive provisions and national provisions, taken from official
correlation tables. They are what a government *asserted* when it sent a bill to parliament:
reference data for scoring automatic matching, not findings about whether a provision is
correctly transposed.

Czech sets (built by `scripts/build_evalset.py` with `openacquis.evalset`; same fields, described
under NIS2 below):

| File | Directive | Bill (sněmovní tisk) | Act checked against | Links | Rows | EU IDs found | Czech IDs found |
|---|---|---|---|---|---|---|---|
| `nis2_cz.jsonl` | NIS2 2022/2555 | 9/759 | 264/2025 Sb. (2025-11-01) | 569 | 479 | 100 % | 100 % |
| `32022L2557_cz.jsonl` | CER 2022/2557 | 9/947 | 266/2025 Sb. (2025-08-19) | 229 | 200 | 100 % | 99.5 % |
| `32019L0882_cz.jsonl` | Accessibility (EAA) 2019/882 | 9/539 | 424/2023 Sb. (2025-07-01) | 146 | 111 | 100 % | 100 % |
| `32019L1937_cz.jsonl` | Whistleblower 2019/1937 | 9/352 | 171/2023 Sb. (2025-02-15) | 685 | 49 | 100 % | 100 % |

File names are `<CELEX>_<country>.jsonl`; NIS2 keeps its original name. The Slovak sets
(`nis2_sk.jsonl`, `cer_sk.jsonl`, `pay_sk.jsonl`) are described in [README_sk.md](README_sk.md); they keep the
`<short>_<country>` names their builder writes. Every set is registered once in
`src/openacquis/goldsets.py` (`openacquis eval --list`). Only `nis2_cz.jsonl` was used to tune the matcher;
the other six are held-out tests ([docs/evaluation.md](../../docs/evaluation.md)). Each has a
`_summary.json` with the counts and the lists of unresolved IDs.

## `nis2_cz.jsonl`: NIS2 (32022L2555) × Czechia, Act No. 264/2025 Sb.

| | |
|---|---|
| Source | *Rozdílová tabulka návrhu právního předpisu s předpisy EU*, attachment "Kompatibilita s právem EU" to the government bill of the Cybersecurity Act (*Vládní návrh zákona o kybernetické bezpečnosti*), Chamber of Deputies print (sněmovní tisk) **759/0**, 9th term, sent to deputies 25 July 2024 |
| Bill history | https://www.psp.cz/sqw/historie.sqw?o=9&t=759 |
| File used | `t0759c0.doc` (Word 97, 1.5 MB, 198 pages): https://www.psp.cz/sqw/text/orig2.sqw?idd=244067 (a PDF of the same table is at `idd=244068`) |
| Retrieved | 2026-10-12 |
| Checked against | NIS2 in Czech from CELLAR (509 provisions) and Act 264/2025 Sb. from e-Sbírka, version in force from 2025-11-01 (736 provisions) |
| Regenerate | `python scripts/build_evalset.py nis2` (stdlib only; `--table FILE` for a local copy, `--no-check` to skip the texts). The generalised importer reproduces this file byte for byte |

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
   link is marked `renumbered`; the bill's ID stays in `bill_national_id`. Safeguards: an ID
   whose whole sentence appears verbatim in the quote is kept (`same_number`, noted; the quote
   may cover many provisions); a renumbering never lands on another ID the same row cites, and
   two IDs of one row never end on the same act ID (the second takes the same shift as the
   first if the act has it, else stays `unverified`); letters of a list are re-aligned in order
   when the act inserted a letter (noted "letters of the list aligned").

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
| `national_check` | `same_number` (text confirms the same ID in the act), `renumbered`, `ancestor` (the table cites a level the act does not have and quotes the text of the provision above it, which is used), `unverified` (ID exists, text does not confirm it) or `unresolved` |
| `national_similarity` | 0–1: how well the quoted bill text supports `national_id` |
| `eu_text`, `national_text` | First 500 characters of the table's text cells (evidence; the bill text, not the act) |
| `eu_act` | Only when set: the CELEX the EU reference is numbered in, when it is not `celex` (a consolidated act: `32013L0034 ve znění 32022L2464` cites Directive 2013/34 as amended by 2022/2464; `eu_id` is then built on 2013/34) |
| `flags` | Only when set: `amendment_point` (the bill reference is a point of a part that amends another act, `ČÁST TŘETÍ bod 1.`), `part_ref` (a whole part, `ČÁST DESÁTÁ`), `consolidated`, `celex_typo` (the table's CELEX cell was corrected), `section_act` (the row belongs to another national act named in a section header of the table) |

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

## Other Czech sets: CER, Accessibility, Whistleblower

Built the same way as the NIS2 set (`python scripts/build_evalset.py cer eaa whistleblower`),
retrieved and checked on 2026-10-12. The tables are official works (*úřední dílo*, § 3 of Act
No. 121/2000 Sb.) quoting EU law (© European Union, reused under Decision 2011/833/EU); the
derived data are CC-BY-4.0.

| | CER 2022/2557 | Accessibility (EAA) 2019/882 | Whistleblower 2019/1937 |
|---|---|---|---|
| Bill | *Vládní návrh zákona o kritické infrastruktuře*, print **947/0**, 9th term, sent 4 April 2025 | Government bill on accessibility requirements for products and services, print **539/0**, 9th term | *Vládní návrh zákona o ochraně oznamovatelů*, print **352/0**, 9th term |
| Bill history | https://www.psp.cz/sqw/historie.sqw?o=9&t=947 | https://www.psp.cz/sqw/historie.sqw?o=9&t=539 | https://www.psp.cz/sqw/historie.sqw?o=9&t=352 |
| Table file | `t0947d0.docx`, https://www.psp.cz/sqw/text/orig2.sqw?idd=253085 (PDF `idd=253086`) | `t0539b0.docx`, https://www.psp.cz/sqw/text/orig2.sqw?idd=232281 (PDF `idd=232284`) | `t0352a0.doc` (Word 97), https://www.psp.cz/sqw/text/orig2.sqw?idd=221044 (PDF `idd=221046`) |
| Passage | Approved in the first reading (§ 90(2)), Senate unchanged | Approved in the first reading (§ 90(2)), Senate unchanged; later amended by 236/2024 Sb. (print 682) | Amended in the second reading (prints 352/2, 352/3) |
| Act | 266/2025 Sb., version in force from 2025-08-19 (393 provisions) | 424/2023 Sb., version from 2025-07-01 (426 provisions; includes 236/2024) | 171/2023 Sb., version from 2025-02-15 (287 provisions) |
| Template | 4 columns, CELEX only in the header (`CELEX 32022L2557`), bill refs prefixed `ČÁST PRVNÍ` | 5 columns, leading tab characters in cells | 5 columns; CELEX cells repeat the CELEX once per EU ref line (`32019L1937 32019L1937`) |
| Table rows used | 200 of 200 data rows | 111 of 115 data rows (4 are for Regulation 2019/1020) | 49 of 49 |
| Gold links (distinct pairs) | 229 (229) | 146 (146) | 685 (683: the two letters of `§ 28 písm. a) až b)` share their § as ancestor) |
| Distinct EU IDs | 94 in 19 articles; all found as printed | 114 in 32 articles; 94 as printed, 20 via fallbacks (`Čl. 3 odst. 5` -> `art3.pt5`; annex points -> the annex, which is parsed whole) | 97 in 23 articles; 95 as printed, 2 via the Art. 5 point fallback |
| Distinct Czech IDs | 202: 160 same number, 41 renumbered, 1 unresolved | 105: 101 same number, 3 renumbered, 1 unverified | 143: 134 same number, 5 renumbered, 1 ancestor, 3 unverified |
| References that did not parse | 0 | 0 | 0 |

### What the newer tables needed

- **Several CELEX numbers in one cell** (`32019L1937\r\r32019L1937`): a row is kept when the
  target CELEX is among them. Before, such rows were dropped (the Whistleblower table lost 20 of
  49 rows) and, in `.doc` files, could shift the row grouping.
- **CELEX in the header only** (the 2025 template of the CER table): four-column rows take the
  CELEX of the header cell.
- **Parts of the bill** (`ČÁST PRVNÍ § 1 odst. 1`): IDs are anchored at the §, so the prefix is
  dropped. Parts that amend other acts are cited by their points (`ČÁST TŘETÍ bod 1.`, 26 CER
  links, flag `amendment_point`) or as a whole (`ČÁST DESÁTÁ`, 2 links, flag `part_ref`). The act
  numbers those changes as `§ 31 bod 1` etc., and its parts are numbered differently from the
  bill's (one part of the bill is not in the act): such a reference is matched by text against
  the §§ and points of the act's parts headed *Změna ...*, considering only candidates that change
  the same § of the amended act.
- **Leading tabs** in cells, `oddíl` (annex sections: an ID level in Czech acts,
  `priloha1.oddil1.bod1`; kept as a qualifier for EU annexes, which are parsed whole), `odrážka`
  (EU indents, `ind4`) and `návětí` (chapeau, a qualifier).
- **Not used by these three sets, tested on the CSRD table (print 9/783):** section headers that
  name another national act (`Změna zákona č. 416/2023 Sb.`, flag `section_act`), consolidated EU
  references (`32013L0034 ve znění 32022L2464`: `eu_act` = 32013L0034, flag `consolidated`) and
  CELEX typos (`23022L2464`, `32022L246`, `32013L32022L2464`, consolidated prefix `02006L0043`;
  flag `celex_typo`). A correction is only made to a CELEX listed elsewhere in the table, never
  between two well-formed numbers (NIS2 `32022L2555` and CER `32022L2557` differ by one digit).

### Renumbering and unresolved IDs

- **CER.** In the main part, the act added a paragraph level: `§ 2 písm. a)–f)` -> `§ 2 odst. 1
  písm. a)–f)` and `§ 10 písm. a)–f)` -> `§ 10 odst. 1 písm. a)–f)`; `§ 2 písm. g)` -> `§ 2 odst. 1
  písm. i)`; `§ 15 odst. 1 písm. o)` -> `§ 14 odst. 1 písm. o)`; `§ 21 odst. 1` -> `§ 22 odst. 1`;
  `§ 22 odst. 1` -> `§ 23 odst. 1`; `ČÁST SEDMÁ § 41 bod 1.` -> `§ 36 bod 1`. Amending parts move
  down one part (bill `ČÁST TŘETÍ` = act part 2, `§ 31`; `ČÁST SEDMÁ` = `§ 35`, where bill points
  21, 22, 26, 48, 59, 68 are act points 13, 15, 19, 36, 39, 40). Unresolved: `ČÁST PÁTÁ bod 2.`
  (the act's `§ 33 bod 2` repeals `písm. f)` instead of rewording it, so the text no longer
  matches).
- **Accessibility.** `§ 21 odst. 2` and `odst. 3` swapped, `§ 28 odst. 8` -> `odst. 7` (the
  act is checked in its version after Act 236/2024, which may account for these). Unverified: `Příloha č. 3` (the declaration of conformity form; its
  text is not in the e-Sbírka fragments).
- **Whistleblower.** `§ 1 odst. 1 písm. a)–c)` -> `§ 1 písm. a)–c)` (§ 1 has no paragraphs).
  `§ 2 odst. 1`: Parliament inserted a new letter b) (offences with fines of at least 100 000 Kč),
  so the bill's b) and c) are the act's c) and d); the letters are aligned in order with the
  letters whose text the row quotes (act b) has no counterpart in the bill and no link). Ancestor:
  `§ 28 písm. a) až b)` (§ 28 of the act has no letters; the table quotes the whole §).
  Unverified: `§ 3 odst. 3 písm. c)` (the quote does not support the letter); `§ 15 písm. j)`
  (the table cites j) but its text cell stops after letter i), so the text check cannot confirm
  it; the act's § 15 does have a letter j)); `§ 21 odst. 2` (the act added words to the
  retention duty the bill quotes, "a dokumenty související s oznámením po dobu 5 let", so the
  quote no longer reaches the threshold; the ID is the same paragraph).

### Known issues

- Everything listed for NIS2 applies: bill not act, coarse checks for lists, no compatibility
  column (`status` is `listed`).
- **Whistleblower rows are coarse.** A row pairs several EU references with a range of Czech
  provisions (the row of `§ 15 písm. a) až j)` lists 18 EU provisions), and every pair becomes a link, so 685 links
  come from 49 rows. Score it at section (§) level, or weight by row.
- **A table reference can be narrower than its own quote.** Whistleblower row 5 cites
  `§ 4 odst. 1 písm. a) až h)` (retaliation measures, Art. 19) but quotes all 13 letters of the
  bill; act letters i) to m) (e.g. *neumožnění odborného rozvoje*, *zásah do práva na ochranu
  osobnosti*) are quoted in full but have no link, because the gold set follows the reference.
  A matcher that links them to Art. 19 is not wrong; widen the gold links if you score recall
  on that article.
- **Amendment points** (CER, 28 links) point to changes of other acts made by the CER act
  (`§ 35 bod 1`: "V § 1 odst. 1 krizového zákona se slova ... nahrazují ..."). They are legitimate
  transposition, but a matcher that reads the amended acts' consolidated texts will find the
  change there, not in Act 266/2025.
- **Companion bills are not covered.** The Whistleblower companion bill (print 353, Act 172/2023)
  and the EAA follow-up (print 682, Act 236/2024) have their own tables.
- **Text-based remapping can be wrong** where an act rewrote a provision: a `renumbered` link
  with `national_similarity` below 0.8 (CER `ČÁST SEDMÁ bod 21.` and `bod 26.`, 0.71 and 0.63)
  deserves a look.
