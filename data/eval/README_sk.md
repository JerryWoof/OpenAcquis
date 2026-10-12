# Evaluation data: Slovakia

Gold links between EU directive provisions and Slovak provisions, taken from the official
correlation tables (*tabuľka zhody návrhu právneho predpisu s právom Európskej únie*) that the
Slovak government attaches to every bill it sends to the National Council (NR SR). They record
what the government *asserted* about its bill. They are reference data for scoring automatic
matching. They are not findings about whether a provision is correctly transposed.

| File | Directive | Bill (NR SR print, 9th term) | Table | Act checked against |
|---|---|---|---|---|
| `nis2_sk.jsonl` | NIS2 (32022L2555) | [508](https://www.nrsr.sk/web/Default.aspx?sid=zakony/cpt&ZakZborID=13&CisObdobia=9&ID=508), became 366/2024 Z. z. | [DocID 553679](https://www.nrsr.sk/web/Dynamic/Download.aspx?DocID=553679) | 69/2018 Z. z. as amended by 366/2024 (Slov-Lex version 20250101) |
| `cer_sk.jsonl` | CER (32022L2557) | [516](https://www.nrsr.sk/web/Default.aspx?sid=zakony/cpt&ZakZborID=13&CisObdobia=9&ID=516), became 367/2024 Z. z. | [DocID 553726](https://www.nrsr.sk/web/Dynamic/Download.aspx?DocID=553726) | 367/2024 Z. z. (version 20250101) |
| `pay_sk.jsonl` | Pay Transparency (32023L0970) | [1142](https://www.nrsr.sk/web/Default.aspx?sid=zakony/cpt&ZakZborID=13&CisObdobia=9&ID=1142), became 76/2026 Z. z. | [DocID 583022](https://www.nrsr.sk/web/Dynamic/Download.aspx?DocID=583022) | 76/2026 Z. z. (version 20260607) |

Each set has a `*_summary.json` with counts, the bill-article map and the lists of unresolved IDs.
Regenerate with `python scripts/build_evalset_sk.py [nis2|cer|pay]` (stdlib only;
`--table FILE` reads a local copy, `--no-check` skips the texts). Downloads are cached under
`OPENACQUIS_CACHE` (`nrsr/`, `slovlex/`, `eurlex/`). Retrieved 2026-10-12.

**Licence.** Slovak copyright law does not protect legislative drafts or their explanatory
material (§ 5 of Act No. 185/2015 Z. z.). The tables quote the directives: © European Union,
https://eur-lex.europa.eu, reused under Commission Decision 2011/833/EU. The derived dataset
is published under CC-BY-4.0, like other OpenAcquis data.

## What the Slovak template adds

The table has ten columns: EU provision, EU text, **method of transposition**, national act,
national provision, national text, **compliance**, notes, **gold-plating flag** and
gold-plating explanation. Every record keeps them:

| Column | Codes | Field |
|---|---|---|
| Spôsob transpozície | `N` normal, `O` with an option, `D` at the state's discretion, `n.a.` no transposition | `method` |
| Zhoda | `Ú` full, `Č` partial, `Ž` none, `n.a.` | `compliance` (as printed) and `status` (`fully_compatible`, `partially_compatible`, `incompatible`, `not_applicable`; `listed` when the cell is empty) |
| Identifikácia goldplatingu | `GP-N` (no gold-plating) in all three tables, `GP-A` … | `gold_plating`, `gold_plating_note` |

A row that names no national provision becomes a **negative record** (`negative: true`, empty
`national_id`). The government is saying that nothing in Slovak law transposes that EU
provision: `n.a.` provisions, such as those addressed to the Commission or to member states as
such, and options it did not take (method `D` or `O` with no provision, status
`no_national_provision` whatever the compliance cell says; `not_applicable` is kept for method
`n.a.`). These are useful as true negatives for gap detection, with one exception: a table may
split one EU paragraph by sentence or subparagraph (`V:2`, `Pododsek 2`) and say that only that
part is not transposed. The EU ID then has positive links too, and the negative is flagged
`partial_negative` (in `flags`; NIS2 5, CER 5). Drop those negatives when you use the set for
gap detection at ID level. The core loader `openacquis.evalset.load_jsonl`
reads these files too (it drops the Slovak-only fields), but it keeps the negatives. Filter on
`national_id` (or use `openacquis.evalset_sk.load_jsonl` and filter on `negative`) before scoring
links.

## How it was built (`openacquis.evalset_sk`)

1. **Reading.** The `.docx` is read with `zipfile` and XML (`evalset.docx_rows`). Data rows are
   ten-cell rows whose first cell is an EU reference (`Č:2, O:2, P:a), P:i`, `Príloha č. 1`).
   Header rows are skipped. So are rows of tables nested in a text cell, which the text cell
   already contains, and the side-by-side copies of the annexes after the main table. An empty
   first cell continues the EU reference above it.
2. **References.** Cells are written as `Č:I, §:17, O:1, P:c), P:1`, one keyword per line
   (`C: 1\n§: 1\nO: 1\nP: a\nP: b`) or `§ 2 / P:j / B:1`. `parse_sk_ref` reads them
   hierarchically: each keyword closes the deeper levels it replaces. A `P` under a letter
   is a sub-point (`písm. c) bod 1`; EU: point (a)(i)), while a letter after a letter is the
   next point. Lists (`O:4 a 5`, `P:d,e`) and ranges (`O:4 až 6`) expand. Sentences (`V:`)
   and subparagraphs (`Pododsek 2`, `2. pododsek`) cannot be expressed in IDs, so they are kept
   in `note` (`EU ref qualifier: pododsek 2`, `SK ref qualifier: veta 2`).
3. **Which act.** `Č:I` is the *article of the amending bill*. Its articles are mapped to the
   acts they amend from the act as promulgated (`bill_articles`). For example, 366/2024 Čl. I
   amends 69/2018 and Čl. VI amends 452/2021. A national ID points into that act **in the
   version produced by the amendment**: `sk:69/2018:par17.ods1.pism_c.bod1` in 69/2018 as
   amended by 366/2024. The article that holds a new act's own §§ is the new act itself.
   Final provisions and amending points (`Č:VI, Bod 5`) point into the promulgated amending
   act (`sk:366/2024:cl6.bod5`). Other acts named in the row (`Zákon č. 575/2001 Z. z.`,
   `Civilný sporový poriadok`) are used in the version in force on the day the amendment took
   effect.
4. **Several acts in one row.** The act cell lists one act per line and the reference cell has
   one group per act. Equal counts are paired in order (`act_assignment: ordered`). Otherwise a
   group gets the act at the nearest relative line position (`position`), which is a guess.
5. **Checking.** EU IDs are looked up in the Slovak language version from CELLAR. The fallbacks
   are: definitions cited as `O:16` map to points (`art6.pt16`), `anx1` maps to `anx_i`, and an
   article without paragraphs drops `par1`. National IDs are first adjusted for known template
   habits: `§:3, O:1, P:q` for a § without paragraphs, `§ 2 P:a` for letters in paragraph 1, an
   unnumbered `Príloha`, and `§: 5 P: f O: 2` where the CER template writes the numbered points
   of a letter as `O:` (`par5.pism_f.bod2`, used only when the act has no such paragraph). Then
   the bill text quoted in the table is compared with the act at that ID, as for Czechia
   (`evalset.check_links`: `same_number`, `renumbered`, `ancestor`, `unverified`,
   `unresolved`). Slovak cells quote many provisions at once, so an ID is also confirmed when a
   whole sentence of its text appears verbatim in the cell; a renumbering never lands on
   another ID of the same row, and two IDs of a row never end on the same act ID. A link that
   its act does not confirm (or that was renumbered inside it) is moved to another act named in
   the row, or to the act of a neighbouring bill article, when that act's text at the same ID
   matches (`note: act changed by text`, `act_assignment: text`). Bill and act article
   numbering can differ. For CER, the bill's Čl. IV became Čl. V (215/2004) of 367/2024.
   Annex texts that Slov-Lex publishes only as PDF are never counted as confirmed.

## Counts (build of 2026-10-12)

| | NIS2 | CER | Pay Transparency |
|---|---:|---:|---:|
| Table data rows | 286 | 175 | 129 |
| Method N / D / O / n.a. | 202 / 16 / 0 / 67 (1 empty) | 117 / 3 / 1 / 52 (1 `D/N`, 1 empty) | 111 / 8 / 0 / 10 |
| Compliance Ú / Č / n.a. / empty | 206 / 3 / 0 / 77 | 122 / 0 / 52 / 1 | 110 / 0 / 16 / 3 |
| Gold-plating flags | `GP-N` only (209) | `GP-N` only (123) | `GP-N` only (129) |
| **Positive links** (distinct pairs) | 470 (469) | 414 (409) | 232 (232) |
| from table rows | 210 | 123 | 111 |
| distinct EU IDs / articles | 206 / 35 | 122 / 23 and the Annex | 111 / 31 |
| distinct national IDs | 256 | 237 | 190 |
| links into the main act | 443 (69/2018) | 326 (367/2024) | 109 (76/2026) |
| other acts | 8 (575/2001, 272/2016, 40/1964, …) | 4 (10/1996, 215/2004, 575/2001, 162/2015) | 19 (311/2001, 125/2006, 365/2004, 160/2015, …) |
| **Negative records** (rows) | 76 (76): 67 `n.a.`, 9 options not used | 52 (52) | 18 (18) |
| EU IDs resolved | 206/206 (100 %; 43 via the Art. 6 point fallback) | 122/122 (100 %; 1 fallback, the Annex) | 111/111 (100 %; 1 fallback) |
| National IDs found in the act | 255/256 (99.6 %) | 237/237 (100 %) | 186/190 (97.9 %) |
| national IDs confirmed by text | 251/256 (98.0 %) | 235/237 (99.2 %) | 183/190 (96.3 %) |
| renumbered IDs / act changed by text | 0 / 0 | 2 / 46 links | 3 / 12 links |
| position-assigned links | 97 | 58 | 48 |

"Found" counts `same_number`, `renumbered`, `ancestor` and `unverified` (the ID exists, but the
text does not confirm it), as in the Czech summary. "Confirmed" leaves out `unverified`. The
remaining IDs are listed in each summary:
- NIS2: `par3.ods1.pism_ac` (a definition the bill had and the act does not).
- CER: none.
- Pay: `35/2019:par18.ods4`, and three IDs of "308/1999 Z. z." (`not_checked`), which Slov-Lex
  has only as a 1999 text. It is probably a typo in the table for 308/1993. The five articles of
  the Constitution the table cites resolve (see Known issues).

## Known issues

- **Bill, not act.** The tables describe the bill as submitted (NIS2 and CER in October 2024, Pay
  Transparency in January 2026). The text check confirms only that the act's provision is the
  one quoted. It cannot see amendments made in parliament that change content but keep the
  number.
- **Whole-row pairs.** One link is written for every (EU ID, national ID) pair of a row. In CER
  especially, several rows pair a whole EU paragraph with long lists (e.g. Art. 1(1)(a)–(e)
  with 15 national provisions), so pair-level recall is not meaningful there. EU-level recall
  is the better measure.
- **Act per group by position.** When a row names fewer acts than it has reference groups,
  the act is guessed from line positions (`act_assignment: position`, 203 links in all). The text
  check catches a wrong guess only when the wrong act lacks the ID or its text differs.
- **Sloppy cells.** The CER template writes the points of `§ 5 písm. f)` as `O:`
  (`§: 5 P: f O: 1 ... O: 6`); § 5 of 367/2024 has no paragraphs, so these are read as
  `par5.pism_f.bod1` ... (noted "O: after a letter read as its point"). `§: 5 P: f O: 9 P: e O: 3`
  gives `par5.pism_e` and `par5.pism_e.bod3`. Row 66 (`§: 5 P: f O: 6 20 O: 5`) has a stray `20`;
  its `O: 5` is renumbered by text to `par20.ods6`.
- **Annexes as PDF.** Annexes 1 and 2 of 69/2018, which hold the NIS2 definitions of sectors and
  services, and Annex 1 of 367/2024 (the CER sectors) are published on Slov-Lex only as PDF.
  Links to them stay `unverified`, with a note, even where the table's own pointer text
  ("viď nižšie tabuľka s prílohou č. 1") resembles the PDF placeholder.
- **CER Annex row.** Row 174 (the directive's Annex) has an empty reference cell; the provision
  is taken from its text cell (`Príloha č. 1 k zákonu ...`, noted), giving `367/2024:priloha1`.
- **`bill_article`** is set only when the row cites the bill (its article, or "v znení návrhu
  zákona"); a link moved to another act by text gets that act's bill article, or none.
- **Constitution.** Slov-Lex marks the articles of the Constitution (460/1992) as
  `ustavnyclanok`; the adapter reads them as články (`460/1992:cl46.ods1`). The five `Čl.` IDs
  of the Pay Transparency table are confirmed by text; `Čl. 127 ods. 1`, which line position puts
  in 365/2004, is moved to the Constitution by text (`act_assignment: text`).
- **Main act vs. other acts.** The sets include links into other acts (laws on the civil
  service, labour inspection, civil procedure …). To score matching against one act, filter on
  `national_act`.
- **No gold-plating positives.** All three tables flag every row `GP-N`. The field is kept for
  other tables (CSRD, EAA and Whistleblower tables exist for the same template).

## Fields (in addition to those of `nis2_cz.jsonl`; see `README.md`)

| Field | Meaning |
|---|---|
| `status` | Normalised compliance; `not_applicable` / `no_national_provision` on negatives |
| `method`, `compliance`, `gold_plating` | Codes as printed (several joined by `/`) |
| `gold_plating_note`, `table_note` | The table's explanation and notes columns (first 500 characters) |
| `national_act` | Act the national ID points into (`69/2018`) |
| `act_version` | Slov-Lex version checked against (`20250101`; `vyhlasene_znenie` for the promulgated amending act) |
| `bill_article` | Article of the amending bill cited (`I`); empty when the table cited a base act directly |
| `act_assignment` | `single`, `ordered`, `position` or `none` (see step 4); `text` when the check moved the link to another act |
| `flags` | `partial_negative` on a negative whose EU ID also has positive links (another sentence or subparagraph of it is transposed) |
| `negative` | `true` for EU provisions the table says nothing transposes |
| `row` | Index of the data row in the table (0-based, header rows excluded) |
