# Provisions: data model and IDs

Every text OpenAcquis reads, whether an EU directive in one language or a national act, becomes a
`Document` (in `openacquis.provisions`). A `Document` holds the act's metadata and a **flat list of
`Provision` objects in document order**, linked by `parent_id`. A flat list is easy to write as
JSON lines and to diff; the tree is rebuilt on demand (`doc.children(id)`, `doc.walk(id)`,
`doc.full_text(id)`).

## Document

| Field | Example (EU) | Example (Czechia) |
|---|---|---|
| `source` | `eu` | `cz` |
| `act_id` | `32022L2555` (CELEX) | `264/2025` (number/year in the Sbírka zákonů) |
| `language` | `cs` (one of the 24 EU languages) | `cs` |
| `title` | `SMĚRNICE EVROPSKÉHO PARLAMENTU A RADY (EU) 2022/2555 …` | `Zákon č. 264/2025 Sb., o kybernetické bezpečnosti` |
| `date` | empty for now | `2025-11-01`: the date this consolidated version took effect, **not** the adoption date |
| `eli` | `http://data.europa.eu/eli/dir/2022/2555/oj` | `/eli/cz/sb/2025/264/2025-11-01` |
| `url` | `https://eur-lex.europa.eu/legal-content/CS/TXT/?uri=CELEX:32022L2555` | `https://e-sbirka.gov.cz/sb/2025/264/2025-11-01` |

## Provision

| Field | Meaning |
|---|---|
| `id` | Stable ID, see below |
| `source`, `act_id`, `language` | Copied from the document, so each JSON line stands on its own |
| `kind` | `article`, `paragraph`, `point`, `subpoint`, `indent`, `recital`, `annex` (EU); `cast`, `hlava`, `dil`, `oddil`, `pododdil`, `par` (§), `cl` (článek), `odst`, `pism`, `bod`, `priloha`, `ppc` (footnote) (Czechia); Slovakia, Austria and Germany: see [National kinds](#national-kinds-v04) |
| `label` | Number as printed: `Article 23`, `4.`, `(a)`, `a)`, `§ 12`, `(2)` |
| `number` | Normalised number used in the ID: `23`, `4`, `a`, `12`, `iv` (Roman numerals are kept, not converted) |
| `text` | The provision's **own** text, without its children's text. Later subparagraphs are appended after a line break |
| `heading` | Article or section heading, e.g. `Reporting obligations`, `Stanovení rozsahu řízení kybernetické bezpečnosti` |
| `parent_id` | ID of the enclosing provision, or empty at the top level |
| `eli` | ELI of the provision where the source has one (e-Sbírka); empty for EU provisions (only the document has an ELI) |
| `url` | Link to the official text at that spot. EU points link to their paragraph's anchor, because EUR-Lex has no anchors for points |

## ID scheme

```
source : act_id [: language] : segment.segment.…
```

Each segment is a short prefix for the kind plus the normalised number. If the number starts with
a letter, an underscore separates them (`pt_a`, `anx_iii`); otherwise there is none (`art23`, `par4`).
IDs depend only on the act and its printed numbering, so they stay the same when a text is fetched
and parsed again.

| Kind | Prefix | Example |
|---|---|---|
| article | `art` | `eu:32022L2555:en:art23` |
| paragraph | `par` | `eu:32022L2555:en:art23.par4` |
| point | `pt` | `eu:32022L2555:cs:art23.par4.pt_a` (Art. 23(4)(a), Czech text) |
| sub-point | `subpt` | `eu:32022L2555:en:art23.par4.pt_d.subpt_i` |
| indent (dash) | `ind` | `eu:31995L0046:en:art3.par2.ind2` (numbered 1, 2, 3 in order) |
| recital | `rct` | `eu:32022L2555:en:rct1` (only with `recitals=True` / `--recitals`) |
| annex | `anx` | `eu:32022L2555:en:anx_iii` (one provision with the annex's flattened text) |
| § | `par` | `cz:264/2025:par12` |
| odstavec | `odst` | `cz:264/2025:par12.odst2` |
| písmeno | `pism` | `cz:264/2025:par12.odst2.pism_b` |
| bod | `bod` | `cz:270/2025:cl1.bod3` |
| článek | `cl` | `cz:1/1993:cl1` |
| příloha | `priloha` | `cz:408/2025:priloha0` (an unnumbered annex gets number 0; numbered ones are `priloha1`, …) |
| footnote | `ppc` | `cz:264/2025:ppc1` |
| část / hlava / díl | `cast`, `hlava`, `dil` | `cz:264/2025:cast1.hlava2.dil1` |

Rules worth knowing:

- **EU IDs carry the language; national IDs do not.** The 24 language versions of a directive have the
  same IDs apart from the language code. This was checked for NIS2: 509 provisions in BG, CS, DE, EL, EN, HU, LV,
  PL, RO and SK, identical apart from the language.
- **Points straight under an article** (no numbered paragraph), as in definitions articles, hang
  off the article: `eu:32022L2555:en:art6.pt1` is Article 6 point (1), and its letters are sub-points
  (`art6.pt1.subpt_a`).
- **Repeated labels**: if the same point label appears twice in one paragraph (two lists in two
  subparagraphs), the second gets `_2`: `pt_a_2`.
- **Paragraphs numbered `(1)`** (as in the German text) are still paragraphs (`par1`); points may be
  printed `(a)`, `a)` (Czech), `(1)`, or `i.` (Hungarian sub-points).
- **Greek and Bulgarian point letters** are mapped to the Latin letter at the same position, so IDs match
  the other languages: Greek `α)`, `β)`, `στ)`, `ια)` (Greek numerals 1, 2, 6, 11) become `pt_a`, `pt_b`,
  `pt_f`, `pt_k`; Bulgarian `а)`, `б)`, `й)` become `pt_a`, `pt_b`, `pt_j`. The printed letter stays in
  `label`. With this, the NIS2 language versions tried (BG, CS, DE, EL, EN, HU, LV, PL, RO, SK) give the
  same IDs. FI differs in one place: its Article 29(1) is printed `1)`, so it is not read as a paragraph.
- **Annexes** are found by their `anx_*` anchor or, where a language version or an older act has none,
  by a title such as `ANNEX II` / `ΠΑΡΑΡΤΗΜΑ II` after the articles; the number comes from the title
  (`anx_ii`), and a single unnumbered `ANNEX` is `anx1`.
- **Text quoted by an amending act** (`‘(17) “sustainability matters” means …’;`, or a whole replaced
  `‘Article 19a …’`) stays text of the amending point, up to the closing quote, as in the Czech
  adapter. Only the amending act's own numbering becomes provisions.
- **Czech IDs start at the § or článek**, whatever part or chapter it sits in, because that is
  how Czech law is cited. Structural units (část, hlava, díl, oddíl) keep their full path. If the same §
  number occurs twice in one act, the second falls back to the full path (`cz:1/2020:cast2.par1`) and
  its odstavce follow it (`cz:1/2020:cast2.par1.odst2`); this has not happened in the acts tested.
- **Czech článek numbers come from the ELI** (`cl1`, `cl2`) even when printed in roman numerals
  (`Čl. I`, `Čl. II`, in `label`). `openacquis law cz 270/2025 -a II` accepts either form.

## National kinds (v0.4)

Every adapter anchors IDs at the unit lawyers cite (§ or article), whatever part or chapter it sits
in, and keeps the full path for structural units. `openacquis.align.path_label` prints a provision as
it is cited in the country.

| Country (`source`) | `act_id` | Kinds (ID prefix) | Example ID | Printed as |
|---|---|---|---|---|
| Slovakia (`sk`) | `69/2018` (number/year in the Zbierka zákonov) | `par` §, `cl` článok, `ods` odsek, `pism` písmeno, `bod`, `priloha`, `pozn` footnote; `cast`, `hlava`, `diel`, `oddiel` | `sk:69/2018:par12.ods3.pism_b` | § 12 ods. 3 písm. b) |
| Austria (`at`) | `20013065` (RIS *Gesetzesnummer* of the consolidated law; the BGBl citation is in the title) | `par` §, `art` Artikel, `anl` Anlage, `abs` Absatz, `z` Ziffer, `lit` litera, `sublit`, `ind` dash item; `teil`, `hauptstueck`, `abschnitt`, `unterabschnitt` | `at:20013065:par32.abs4.lit_a` | § 32 Abs. 4 lit. a |
| Germany (`de`) | `bsig_2025` (gesetze-im-internet.de slug, from the official abbreviation) | `par` §, `art` Artikel, `abs` Absatz, `nr` Nummer, `buchst` Buchstabe, `dbuchst` Doppelbuchstabe, `strich` dash, `anlage`, `anhang`, `praeambel`; `teil`, `kapitel`, `abschnitt` … | `de:bsig_2025:par28.abs1.nr3.buchst_a` | § 28 Abs. 1 Nr. 3 Buchst. a |

Country rules:

- **Slovakia.** An unnumbered odsek or bod is folded into its parent: `sk:69/2018:par1.pism_a` is
  § 1 písm. a). A "Čl. I" that wraps all §§ of an act is not part of the ID or the printed citation.
  Amending articles keep theirs (`sk:69/2018:cl3.bod1`). Annexes published only as PDF hold their
  link text and a `PDF: <url>` line.
  - A group heading printed above several §§ applies to every § in the group that has no heading of
    its own; when a nested group ends, the outer group's heading applies again.
  - A footnote quoted by an amending point is text of that point (with its marker, e.g. `27g)`), not
    a `pozn` provision.
  - A version reference `69/2018-366/2024` means the latest consolidated version produced by the
    amending act 366/2024 that is already in force (an amending act can take effect in stages); if
    none has started yet, its first version.
- **Austria.** An Austrian law that nests §§ inside Artikel gets `art1.par2`. Hauptstück and
  Abschnitt units nest inside the Artikel too (DSG: `at:10001597:art2.hauptstueck1.abschnitt1`, the
  parent of `art2.par4`). `openacquis.adapters.at.provision_id(doc, "par4.abs1")` turns an ID tail
  without the Artikel (as CELLAR measure titles cite it) into the full ID, or `None` when it is missing
  or ambiguous; `openacquis law at DSG -s 4` uses it. A "(Verfassungsbestimmung)" marker stays in the
  text of its Absatz. Absätze and items have no ELI of their own and link to their § page; laws
  promulgated before 2004 have no document ELI either (`Document.eli` is `None`), since RIS has none
  for those BGBl issues.
  - Numbered lists tagged `aufzaehlung` or `ziffernliste` both become `z` provisions.
  - A later list in the same Absatz whose numbers clash with an earlier one gets `_2` on every item
    (`z1_2`, `z2_2`, …), as in Germany.
- **Germany.** A second list in the same Absatz whose numbers clash with the first gets `_2` on every
  item, as in EU IDs (BSIG-style: `nr1`, `nr2`, then `nr1_2` … `nr5_2`). Only the current text
  exists, so there are no versions.

The matcher (`openacquis.match`) treats all national § / Artikel / Anlage kinds as article level and
Absatz / odsek / odstavec as paragraph level. It skips structural units and footnotes.

## Examples

```python
from openacquis.eurlex import EurLexClient
from openacquis.adapters import fetch_act

nis2 = EurLexClient().document("32022L2555", "cs")
print(nis2.full_text("eu:32022L2555:cs:art23.par4"))

act = fetch_act("264/2025")                     # current consolidated version
p = act.by_id("cz:264/2025:par12.odst2.pism_b")
print(p.label, p.text)                          # b) posoudí, zda primární aktiva souvisí …
```

One provision as JSON (`openacquis law cz 264/2025 --id par12.odst2.pism_b -f jsonl`, live output,
reformatted; `-f json` wraps the same objects in the document's metadata):

```json
{
  "id": "cz:264/2025:par12.odst2.pism_b",
  "source": "cz",
  "act_id": "264/2025",
  "language": "cs",
  "kind": "pism",
  "label": "b)",
  "number": "b",
  "text": "posoudí, zda primární aktiva souvisí s poskytováním regulované služby, a",
  "heading": null,
  "parent_id": "cz:264/2025:par12.odst2",
  "eli": "/eli/cz/sb/2025/264/2025-11-01/dokument/norma/cast_1/hlava_2/dil_2/par_12/odst_2/pism_b",
  "url": "https://e-sbirka.gov.cz/sb/2025/264/2025-11-01#par_12-odst_2-pism_b"
}
```

Serialisation: `doc.to_json()` / `Document.from_json()` for a whole document, `doc.to_jsonl()` /
`provisions_from_jsonl()` for one provision per line. On the command line use `-f json` or `-f jsonl`.

## Known limitations (v0.2; Czech adapter and EU texts)

- **Text after a list.** A subparagraph that follows a list of points is stored in the paragraph's own
  text, after a line break, so the text output prints it before the points. Its position relative to the
  points is not kept.
- **Older EU acts** (no XHTML, plain HTML only): an article with no heading whose first line is short and
  has no closing punctuation may have that line taken as the heading. Headings printed on the article
  line (`Article 1 Objective`) are recognised. Chapter and section headings are dropped when they start
  with a keyword (`CHAPTER II`, `Section 2: …`, `I LUKU`) or are a line of capital letters without digits.
  Letters that are also roman numerals (`(i)`, `(v)`, `(x)`) are placed by sequence: `(i)` after `(h)`
  is a point unless `(ii)` follows, `(c)` after `(b)(ii)` is the next point.
- **Quoted amending text in older plain-HTML acts** is not detected; its points may become provisions.
- **EU `Document.date`** is empty and `title` is the title lines joined; CELLAR metadata (`openacquis summary`)
  has the date.
- **Czech group headings** printed above several §§ are attached only to the next § (if it has no heading
  of its own).
- **Czech amending acts**: text they insert into another law is stored as the text of the amending `bod`,
  not as separate provisions. The title block, preamble and signatures are not turned into provisions.
- **Czech annex tables** are flattened to rows with cells separated by ` | `.
