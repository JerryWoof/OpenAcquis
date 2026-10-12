"""Gap and extra-rule candidates: what is left over after matching (v0.4).

After :mod:`openacquis.match` has scored every EU unit against every national
unit, two lists are worth a reviewer's time:

* **gap candidates**: EU provisions for which nothing similar was found in the
  national acts searched (maybe not transposed, maybe transposed elsewhere);
* **extra-rule candidates**: provisions of the main transposing act that match
  nothing in the directive (maybe national procedure, maybe "gold-plating").

v0.3 took every unlinked unit on either side. On NIS2 x Czechia that gave 118 gap
candidates of which 66 are listed in the official correlation table: not usable.
v0.4 filters both lists with document structure and scores, in four steps that
need no model and work in every language with a keyword list below (cs, sk, de,
en; other languages skip the keyword filters):

1. **Scope.** EU provisions that no member state transposes are set aside, not
   reported as gaps: final provisions (transposition deadline, amendments of other
   EU acts, repeal, entry into force, addressees, delegated acts, committee
   procedure, review clause, correlation table), and units addressed only to EU
   bodies (the Commission, ENISA) with no mention of member states, national
   authorities or entities. Only *unlinked* units are tested and set aside; a link
   on a unit the test would set aside is kept and reported in
   ``eu_linked_out_of_scope``. On the national side,
   boilerplate is set aside: definitions, entry into force, transitional and repeal
   clauses, enabling clauses for implementing acts, penalty lists (they implement
   the directive's sanctions article through cross-references, not shared wording),
   amendments of other acts and transposition notes.
2. **Score margin.** A gap candidate's best score must stay below ``gap_threshold``,
   a little under the link threshold: a unit that just misses the link threshold
   usually has a matching provision (the band between the two is "uncertain", not a gap).
   An extra-rule candidate's best score against the whole directive must stay below
   ``extra_threshold``.
3. **Structure.** A paragraph lead-in is not a gap when one of its points is linked
   (and a point not when its lead-in is, which covers lists the act restates as a
   whole). An extra-rule candidate's § must have no unit linked to the directive:
   a § that partly transposes an article is not an extra rule, even if one of its
   sub-points matches nothing.
4. **Grouping.** Candidates are also reported per EU article (``gap_articles``) and
   per national § (``extra_rule_sections``), which is the level a reviewer reads.

Everything stays a ``status: "candidate"`` with its evidence (best match and score).
A gap candidate never means "not transposed", and an extra-rule candidate never means
"gold-plating"; see docs/evaluation.md for how often they agree with an official table.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field

from .match import MatchResult, level_id
from .provisions import Document, Provision

STATUS = "candidate"


def _fold(text: str | None) -> str:
    """Lower case without diacritics and with single spaces (keyword matching)."""
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.replace("ß", "ss").split())


def _rx(*words: str) -> re.Pattern:
    return re.compile(r"(?:^|\b)(?:" + "|".join(words) + r")")


# --- EU side: provisions no member state transposes ----------------------------------

#: Article headings of the final provisions, per language (folded: no diacritics, lower case).
EU_FINAL_HEADINGS: dict[str, re.Pattern] = {
    "cs": _rx(r"provedeni ve vnitrostatnim", r"zmena (narizeni|smernice|rozhodnuti)", r"zruseni\b",
              r"vstup v platnost", r"urceni$", r"vykon prenesene pravomoci", r"postup projednavani ve vyboru",
              r"prezkum$", r"srovnavaci tabulka"),
    "sk": _rx(r"transpozicia", r"zmena (nariadenia|smernice|rozhodnutia)", r"zrusenie\b",
              r"nadobudnutie ucinnosti", r"adresati", r"vykonavanie delegovania", r"postup vyboru",
              r"preskumanie$", r"tabulka zhody"),
    "de": _rx(r"umsetzung$", r"anderung der (verordnung|richtlinie|entscheidung)", r"aufhebung$",
              r"inkrafttreten", r"adressaten", r"ausubung der befugnisubertragung", r"ausschussverfahren",
              r"uberprufung$", r"entsprechungstabelle"),
    "en": _rx(r"transposition$", r"amendments? (to|of) (regulation|directive|decision)", r"repeal$",
              r"entry into force", r"addressees", r"exercise of the delegation", r"committee procedure",
              r"review$", r"correlation table"),
}

#: EU bodies (subjects of provisions no member state has to transpose): the Commission and ENISA.
#: The Cooperation Group, the CSIRTs network and EU-CyCLONe are deliberately *not* listed: they are
#: made up of member-state representatives (national authorities, national CSIRTs), so their tasks
#: involve national participation that a transposing act commonly provides for (the Czech
#: correlation table maps most of NIS2 Art. 14(4) and 15(3) to Czech provisions). A unit that
#: names only them is therefore not set aside; see :data:`JOINT_BODIES`.
EU_BODIES: dict[str, re.Pattern] = {
    "cs": _rx(r"komise", r"enisa"),
    "sk": _rx(r"komisi\w*", r"enisa"),
    "de": _rx(r"kommission", r"enisa"),
    "en": _rx(r"commission", r"enisa"),
}

#: Union bodies made up of member-state representatives (not used to set units aside; kept for
#: reference and for callers that want to flag them).
JOINT_BODIES: dict[str, re.Pattern] = {
    "cs": _rx(r"skupin\w* pro spolupraci", r"sit\w* csirt", r"eu-cyclone"),
    "sk": _rx(r"skupin\w* pre spolupracu", r"siet\w* (jednotiek )?csirt", r"eu-cyclone"),
    "de": _rx(r"kooperationsgruppe", r"csirts?-netzwerk", r"eu-cyclone"),
    "en": _rx(r"cooperation group", r"csirts network", r"eu-cyclone"),
}

#: Words showing that a unit concerns member states, their authorities or regulated entities.
NATIONAL_ACTORS: dict[str, re.Pattern] = {
    "cs": _rx(r"clensk\w* stat", r"prislusn\w* organ", r"subjekt", r"tym\w* csirt",
              r"jednotn\w* kontaktn\w* mist", r"poskytovatel"),
    "sk": _rx(r"clensk\w* stat", r"prislusn\w* organ", r"subjekt", r"jednotn\w* kontaktn\w* miest",
              r"poskytovatel"),
    "de": _rx(r"mitgliedstaat", r"zustandig\w* behorde", r"einrichtung", r"zentral\w* anlaufstell",
              r"anbieter"),
    "en": _rx(r"member state", r"competent authorit", r"entities", r"entity", r"single point", r"provider"),
}


def eu_scope(doc: Document, provision_id: str, context: str = "") -> str | None:
    """Why an EU unit needs no national transposition, or ``None`` if it may.

    ``"final_provisions"`` (by article heading) or ``"union_level"`` (only the Commission
    or ENISA named in the unit's text and ``context``, no member state, national authority
    or entity; units about the Cooperation Group, the CSIRTs network or EU-CyCLONe are not
    union-level, see :data:`EU_BODIES`). Languages without keyword lists always give ``None``.
    """
    lang = doc.language or ""
    finals = EU_FINAL_HEADINGS.get(lang)
    if finals is None:
        return None
    p = doc.by_id(provision_id)
    art = doc.get(level_id(doc, provision_id, "article"))
    heading = _fold(art.heading if art is not None else p.heading)
    if heading and finals.search(heading):
        return "final_provisions"
    text = _fold(" ".join(x for x in (context, doc.full_text(provision_id, sep=" ")) if x))
    if EU_BODIES[lang].search(text) and not NATIONAL_ACTORS[lang].search(text):
        return "union_level"
    return None


# --- national side: boilerplate ------------------------------------------------------

#: Category -> pattern over the folded headings of a provision and its ancestors.
NATIONAL_BOILERPLATE_HEADINGS: dict[str, dict[str, re.Pattern]] = {
    "definitions": {
        "cs": _rx(r"vymezeni (zakladnich )?pojmu", r"zakladni pojmy", r"definice"),
        "sk": _rx(r"vymedzenie (zakladnych )?pojmov", r"zakladne pojmy"),
        "de": _rx(r"begriffsbestimmung"),
        "en": _rx(r"definitions"),
    },
    "entry_into_force": {
        "cs": _rx(r"ucinnost"),
        "sk": _rx(r"ucinnost\b", r"ucinnost$"),
        "de": _rx(r"inkrafttret", r"ausserkrafttret"),
        "en": _rx(r"entry into force", r"commencement"),
    },
    "transitional": {
        "cs": _rx(r"prechodn"),
        "sk": _rx(r"prechodn"),
        "de": _rx(r"ubergang"),
        "en": _rx(r"transitional"),
    },
    "repeal": {
        "cs": _rx(r"zrusovaci"),
        "sk": _rx(r"zrusovaci"),
        "de": _rx(r"aufhebung (von|des|der) "),
        "en": _rx(r"repeals?$"),
    },
    "enabling": {
        "cs": _rx(r"zmocnovaci", r"zmocneni"),
        "sk": _rx(r"splnomocnovaci"),
        "de": _rx(r"verordnungsermachtigung", r"ermachtigung zum erlass", r"^vollziehung$", r"^verweisungen$",
                  r"personenbezogene bezeichnungen"),
        "en": _rx(r"power to make regulations", r"delegated powers"),
    },
    "penalties": {
        "cs": _rx(r"prestup", r"spravni delikt", r"sankc"),
        "sk": _rx(r"priestup", r"spravne delikt", r"sankci", r"poriadkova pokuta"),
        "de": _rx(r"bussgeld", r"strafbestimm", r"verwaltungsstraf", r"zuwiderhandlung"),
        "en": _rx(r"offences", r"penalties"),
    },
    "transposition_note": {
        "cs": _rx(r"zapracov"),
        "sk": _rx(r"zoznam preberanych", r"preberane pravne"),
        "de": _rx(r"umsetzung von rechtsakten"),
        "en": _rx(r"implementation of eu law"),
    },
}

#: Category -> pattern over the folded text of a provision (when its heading says nothing).
NATIONAL_BOILERPLATE_TEXT: dict[str, dict[str, re.Pattern]] = {
    "entry_into_force": {
        "cs": _rx(r"(zakon|vyhlaska|narizeni) nabyva ucinnosti"),
        "sk": _rx(r"(zakon|vyhlaska|nariadenie) nadobuda ucinnost"),
        "de": _rx(r"tritt (am|mit) .{0,60}in kraft"),
        "en": _rx(r"comes into force"),
    },
    "amendment": {
        "cs": _rx(r"se meni (a doplnuje )?takto", r"se meni a doplnuje"),
        "sk": _rx(r"sa meni a doplna", r"sa meni takto", r"sa dopl\w+ takto"),
        "de": _rx(r"wird wie folgt geandert", r"geandert durch artikel"),
        "en": _rx(r"is amended as follows"),
    },
    "transposition_note": {
        "cs": _rx(r"tento zakon zapracovava", r"timto zakonem se zapracovav"),
        "sk": _rx(r"tymto zakonom sa preberaju", r"zoznam preberanych"),
        "de": _rx(r"dient der umsetzung der richtlinie"),
        "en": _rx(r"this act transposes"),
    },
}

#: Kinds that are never extra rules (footnotes, annexes listing transposed acts are caught by text).
FOOTNOTE_KINDS = {"ppc", "pozn"}
ANNEX_KINDS = {"priloha", "anl", "anlage", "anhang"}
_PDF_ONLY = re.compile(r"(^|\n)pdf: |als pdf dokumentiert|prevziat prilohu")


def _ancestry(doc: Document, p: Provision) -> list[Provision]:
    out, seen = [], set()
    while p is not None and p.id not in seen:
        seen.add(p.id)
        out.append(p)
        p = doc.get(p.parent_id) if p.parent_id else None
    return out


def national_boilerplate(doc: Document, provision_id: str) -> str | None:
    """Category of a national provision that is not a substantive rule, or ``None``.

    Checks the headings of the provision and every enclosing unit (§, část, Teil ...),
    then the text of the provision and its enclosing provisions (amending articles such as
    Slovak "Čl. II Zákon č. … sa mení a dopĺňa takto:"). Languages without keyword lists
    only drop footnotes.
    """
    p = doc.by_id(provision_id)
    if p.kind in FOOTNOTE_KINDS:
        return "footnote"
    if p.kind in ANNEX_KINDS and _PDF_ONLY.search(_fold(p.text)):
        return "no_text"  # an annex published only as PDF: nothing to compare
    lang = doc.language or ""
    chain = _ancestry(doc, p)
    headings = [_fold(a.heading) for a in chain if a.heading]
    for cat, by_lang in NATIONAL_BOILERPLATE_HEADINGS.items():
        rx = by_lang.get(lang)
        if rx is not None and any(rx.search(h) for h in headings):
            return cat
    # amending articles: an article (Čl. II ...) whose first words amend another act
    for a in chain:
        if a.kind in ("cl", "art") and a.number not in ("1", "i"):
            rx = NATIONAL_BOILERPLATE_TEXT["amendment"].get(lang)
            first = _fold(" ".join(x for x in (a.heading, a.text) if x))[:3000]  # headings list past amendments
            if rx is not None and rx.search(first):
                return "amendment"
    texts = [_fold(a.text)[:400] for a in chain[:3] if a.text]
    for cat, by_lang in NATIONAL_BOILERPLATE_TEXT.items():
        rx = by_lang.get(lang)
        if rx is not None and any(rx.search(t) for t in texts):
            return cat
    return None


# --- candidates ------------------------------------------------------------------------


@dataclass
class GapCandidate:
    """An EU unit for which no similar national provision was found."""

    eu_id: str
    best_national_id: str | None
    best_score: float | None
    margin: float  # gap_threshold - best_score: how far below the bar the best match stays
    article_id: str
    article_gap: bool  # no unit of the whole EU article has a link
    status: str = STATUS

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExtraRuleCandidate:
    """A unit of the main transposing act that matches nothing in the directive."""

    national_id: str
    section_id: str
    best_eu_id: str | None
    best_score: float | None
    margin: float  # extra_threshold - best_score
    status: str = STATUS

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Leftovers:
    gap_candidates: list[GapCandidate] = field(default_factory=list)
    extra_rule_candidates: list[ExtraRuleCandidate] = field(default_factory=list)
    #: unlinked EU units set aside: {eu_id: "final_provisions" | "union_level"}
    eu_out_of_scope: dict[str, str] = field(default_factory=dict)
    #: linked EU units the scope test would set aside (links worth a second look):
    #: {eu_id: "final_provisions" | "union_level"}
    eu_linked_out_of_scope: dict[str, str] = field(default_factory=dict)
    #: national units set aside: {national_id: category}
    national_boilerplate: dict[str, str] = field(default_factory=dict)
    #: unlinked EU units that are not gap candidates: {eu_id: reason}
    eu_unlinked_not_gap: dict[str, str] = field(default_factory=dict)
    gap_threshold: float = 0.0
    extra_threshold: float = 0.0
    #: why no gap list was produced (``find_leftovers(..., gaps=False)``), else ``None``
    gap_note: str | None = None

    def gap_articles(self) -> list[dict]:
        """EU articles with gap candidates: ``{article_id, units, whole_article}``, document order."""
        out: dict[str, dict] = {}
        for g in self.gap_candidates:
            a = out.setdefault(g.article_id, {"article_id": g.article_id, "units": [], "whole_article": g.article_gap,
                                              "status": STATUS})
            a["units"].append(g.eu_id)
        return list(out.values())

    def extra_rule_sections(self) -> list[dict]:
        """National §§ with extra-rule candidates: ``{section_id, units}``, document order."""
        out: dict[str, dict] = {}
        for x in self.extra_rule_candidates:
            s = out.setdefault(x.section_id, {"section_id": x.section_id, "units": [], "status": STATUS})
            s["units"].append(x.national_id)
        return list(out.values())


def _row_best(row: Sequence[float]) -> tuple[int, float]:
    best_j, best = -1, float("-inf")
    for j, s in enumerate(row):
        if s > best:
            best_j, best = j, s
    return best_j, best


def find_leftovers(
    mr: MatchResult,
    links: dict[str, Sequence[str]],
    *,
    threshold: float,
    acts: Iterable[str],
    gap_margin: float = 0.01,
    extra_margin: float = 0.0,
    scope_filter: bool = True,
    boilerplate_filter: bool = True,
    structure: bool = True,
    gaps: bool = True,
    gap_note: str | None = None,
) -> Leftovers:
    """Gap and extra-rule candidates from a match result and the predicted links.

    ``links`` maps each EU unit ID to its predicted national IDs (empty = unlinked).
    ``acts`` are the act IDs whose units may be extra-rule candidates (the main
    transposing act). ``gap_margin`` / ``extra_margin`` lower the thresholds for
    gaps and extra rules below the link threshold. The filters can be switched off
    one by one to measure them (``scope_filter``, ``boilerplate_filter``, ``structure``);
    with all three off and both margins 0 the result is the v0.3 behaviour.

    ``gaps=False`` produces no gap candidates (scores without an absolute level, such as
    reciprocal rank fusion, cannot tell an unmatched unit from a matched one); unlinked
    units that pass the scope filter are then listed in ``eu_unlinked_not_gap`` as
    ``"no_gap_list"`` and ``gap_note`` (default: a generic reason) says why.
    """
    eu = mr.eu_doc
    acts = set(acts)
    gap_t = threshold - gap_margin
    extra_t = threshold - extra_margin
    out = Leftovers(gap_threshold=round(gap_t, 6), extra_threshold=round(extra_t, 6))
    if not gaps:
        out.gap_note = gap_note or "no gap list: the scores have no absolute level"
    linked_eu = {e for e, ns in links.items() if ns}
    unit_ids = [u.id for u in mr.eu_units]
    unit_set = set(unit_ids)
    ctx = {u.id: u.text[: max(0, len(u.text) - len(u.body))] for u in mr.eu_units}

    # articles with at least one linked unit
    art_of = {u: level_id(eu, u, "article") for u in unit_ids}
    linked_articles = {art_of[u] for u in linked_eu if u in art_of}

    for i, u in enumerate(mr.eu_units):
        if u.id in linked_eu:
            if scope_filter:
                why = eu_scope(eu, u.id, ctx.get(u.id, ""))
                if why:
                    out.eu_linked_out_of_scope[u.id] = why
            continue
        if scope_filter:
            why = eu_scope(eu, u.id, ctx.get(u.id, ""))
            if why:
                out.eu_out_of_scope[u.id] = why
                continue
        if not gaps:
            out.eu_unlinked_not_gap[u.id] = "no_gap_list"
            continue
        j, best = _row_best(mr.scores[i]) if mr.scores[i] else (-1, float("-inf"))
        if structure:
            # a lead-in whose points are linked, or a point whose lead-in (an ancestor unit) is linked
            below = any(x in linked_eu for x in unit_ids if x.startswith(u.id + "."))
            above = any(a in linked_eu for a in u.ancestors if a in unit_set)
            if below or above:
                out.eu_unlinked_not_gap[u.id] = "part_linked" if below else "parent_linked"
                continue
        if j >= 0 and best >= gap_t:
            out.eu_unlinked_not_gap[u.id] = "near_threshold"
            continue
        out.gap_candidates.append(GapCandidate(
            eu_id=u.id,
            best_national_id=mr.national_units[j].id if j >= 0 else None,
            best_score=round(best, 6) if j >= 0 else None,
            margin=round(gap_t - best, 6) if j >= 0 else gap_t,
            article_id=art_of[u.id],
            article_gap=art_of[u.id] not in linked_articles,
        ))

    # extra-rule candidates: units of the main act(s)
    linked_nat = {n for ns in links.values() for n in ns}
    nat_docs = mr._nat_doc
    n = len(mr.national_units)
    best_i = [-1] * n
    best_s = [float("-inf")] * n
    for i, row in enumerate(mr.scores):
        for j, s in enumerate(row):
            if s > best_s[j]:
                best_i[j], best_s[j] = i, s
    section_of = {}
    for u in mr.national_units:
        d = nat_docs.get(u.id)
        section_of[u.id] = level_id(d, u.id, "article") if d is not None else u.id
    linked_sections = {section_of[x] for x in linked_nat if x in section_of}
    for j, u in enumerate(mr.national_units):
        d = nat_docs.get(u.id)
        if d is None or d.act_id not in acts or u.id in linked_nat:
            continue
        if boilerplate_filter:
            cat = national_boilerplate(d, u.id)
            if cat:
                out.national_boilerplate[u.id] = cat
                continue
        if best_i[j] >= 0 and best_s[j] >= extra_t:
            continue
        if structure and section_of[u.id] in linked_sections:
            continue
        out.extra_rule_candidates.append(ExtraRuleCandidate(
            national_id=u.id,
            section_id=section_of[u.id],
            best_eu_id=mr.eu_units[best_i[j]].id if best_i[j] >= 0 else None,
            best_score=round(best_s[j], 6) if best_i[j] >= 0 else None,
            margin=round(extra_t - best_s[j], 6) if best_i[j] >= 0 else extra_t,
        ))
    return out
