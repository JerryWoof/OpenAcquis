"""National text adapters: fetch consolidated national law and split it into provisions.

Each adapter returns a :class:`openacquis.provisions.Document`. Adapters are
stdlib-only, take an injectable fetcher for offline tests, and cache politely.

:data:`ADAPTERS` is the registry the command line and :mod:`openacquis.align` use:
one :class:`Adapter` per country with its language, portal, client class and how
CELLAR's notified measures map to references the adapter can fetch
(:func:`map_measures`).

=======  ==========  ========  =========================================  ==================
code     country     language  portal                                     act ID example
=======  ==========  ========  =========================================  ==================
``cz``   Czechia     cs        e-Sbírka (JSON API)                        ``264/2025``
``sk``   Slovakia    sk        Slov-Lex (static HTML)                     ``69/2018``
``at``   Austria     de        RIS OGD (search API + XML)                 ``20013065``
``de``   Germany     de        gesetze-im-internet.de (+ NeuRIS)          ``bsig_2025``
=======  ==========  ========  =========================================  ==================
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from . import at, de, sk
from .at import RisClient
from .cz import ESbirkaClient, fetch_act, parse_fragments, parse_reference
from .de import GiiClient
from .sk import SlovLexClient


@dataclass(frozen=True)
class Adapter:
    """What the rest of OpenAcquis needs to know about one country's adapter."""

    code: str  # ISO alpha-2, lower case: the Provision.source and the CLI argument
    alpha3: str  # ISO alpha-3, as CELLAR names member states
    name: str
    language: str  # language of the national text = language the directive is fetched in
    portal: str
    portal_url: str
    module: str  # module under openacquis.adapters
    client: str  # client class name in that module
    #: kind used by ``openacquis law <cc> --article N``: "cl" (Czech/Slovak článok, roman numerals
    #: accepted) or "art" (Austrian/German Artikel)
    article_kind: str = "art"
    #: how an act is cited after its number in labels ("264/2025 Sb.")
    act_suffix: str = ""
    example: str = ""
    #: dated consolidated versions can be requested
    versions: bool = True
    #: the light Czech stemmer suits the language (cs, sk); German is matched unstemmed
    stem: bool = True
    version_help: str = "YYYY-MM-DD"

    def load(self) -> Any:
        return importlib.import_module(f"{__name__}.{self.module}")

    def make_client(self, *args: Any, **kwargs: Any) -> Any:
        """A new client (``fetch=`` stubs the network, as for every adapter)."""
        return getattr(self.load(), self.client)(*args, **kwargs)

    def to_dict(self) -> dict:
        return asdict(self)


ADAPTERS: dict[str, Adapter] = {
    "cz": Adapter("cz", "CZE", "Czechia", "cs", "e-Sbírka", "https://e-sbirka.gov.cz", "cz", "ESbirkaClient",
                  article_kind="cl", act_suffix=" Sb.", example="264/2025",
                  version_help="YYYY-MM-DD; 0000-00-00 = as promulgated"),
    "sk": Adapter("sk", "SVK", "Slovakia", "sk", "Slov-Lex", "https://www.slov-lex.sk", "sk", "SlovLexClient",
                  article_kind="cl", act_suffix=" Z. z.", example="69/2018 or 69/2018-366/2024",
                  version_help="YYYY-MM-DD (in force on), YYYYMMDD (Slov-Lex version id); 0000-00-00 = as promulgated"),
    "at": Adapter("at", "AUT", "Austria", "de", "RIS", "https://www.ris.bka.gv.at", "at", "RisClient",
                  article_kind="art", act_suffix="", example="20013065, 'NISG 2026' or 'BGBl. I Nr. 94/2025'",
                  version_help="YYYY-MM-DD (text in force on that day)"),
    "de": Adapter("de", "DEU", "Germany", "de", "gesetze-im-internet.de", "https://www.gesetze-im-internet.de",
                  "de", "GiiClient", article_kind="art", act_suffix="", example="bsig_2025 or BSIG",
                  versions=False, stem=False, version_help="not supported (current text only)"),
}


def get_adapter(country: str) -> Adapter:
    """The adapter for ``cz`` / ``CZ`` / ``CZE`` ...; ``ValueError`` naming the available ones."""
    c = country.strip()
    for a in ADAPTERS.values():
        if c.lower() == a.code or c.upper() == a.alpha3:
            return a
    have = ", ".join(a.code.upper() for a in ADAPTERS.values())
    raise ValueError(f"no national-law adapter for {country} yet (available: {have})")


# --- notified measures -> adapter references ------------------------------------------


@dataclass
class MeasureMapping:
    """One measure CELLAR lists as notified, mapped to what the adapter can fetch.

    ``status``: ``mapped`` (``ref`` can be passed to the client's ``document``), ``no_reference``
    (nothing citable found), ``land`` (German Land law, not on the federal portal),
    ``not_found``, ``not_a_law`` (explanatory documents ...) or ``error``. ``sections`` names
    the parts of a large act the measure cites (Austria: ``["§ 45", "§ 58"]``); empty = whole act.
    """

    uri: str
    title: str | None
    reference: str | None
    status: str
    ref: str | None = None
    version: str | None = None
    sections: list[str] = field(default_factory=list)
    note: str = ""
    abbreviation: str | None = None  # Austria: tells apart laws promulgated in one BGBl issue

    def to_dict(self) -> dict:
        return asdict(self)


def fetch_measure(country: str, client: Any, mapping: MeasureMapping, version: str | None = None) -> Any:
    """The :class:`~openacquis.provisions.Document` a mapped measure points to.

    Austria fetches only the cited §§ of a large act (``mapping.sections``) and uses the
    abbreviation to pick the right law of a BGBl issue; the others fetch ``mapping.ref``.
    """
    if mapping.status != "mapped" or not mapping.ref:
        raise LookupError(f"measure {mapping.uri} is not mapped to an act ({mapping.status}: {mapping.note})")
    a = get_adapter(country)
    if a.code == "at":
        ref = at.parse_reference(mapping.ref, version)
        if mapping.abbreviation and ref.abbreviation is None:
            ref = at.ActRef(ref.gesetzesnummer, ref.bgbl, mapping.abbreviation, ref.version)
        return client.document(ref, version, mapping.sections or None)
    return client.document(mapping.ref, version or mapping.version)


_CZ_REF = re.compile(r"^\s*(\d+[a-z]?)\s*/\s*(\d{4})\s*$")


def _cz_map(m: Any) -> MeasureMapping:
    ref = (m.reference or "").strip()
    if _CZ_REF.match(ref):
        return MeasureMapping(m.uri, m.title, m.reference, "mapped", ref)
    t = re.search(r"č\.\s*(\d+[a-z]?)\s*/\s*(\d{4})\s*Sb\.", m.title or "")
    if t:
        return MeasureMapping(m.uri, m.title, m.reference, "mapped", f"{t[1]}/{t[2]}", note="number read from title")
    return MeasureMapping(m.uri, m.title, m.reference, "no_reference", note="no Sbírka number in reference or title")


def _sk_map(m: Any) -> MeasureMapping:
    r = sk.measure_reference(m.reference, m.title)
    if r is None:
        return MeasureMapping(m.uri, m.title, m.reference, "no_reference",
                              note="not an act of the Collection of Laws (Z. z. / Zb.)")
    note = f"consolidated as amended by {r.amended_by}" if r.amended_by else ""
    return MeasureMapping(m.uri, m.title, m.reference, "mapped", r.ref, note=note)


def _at_map(m: Any) -> MeasureMapping:
    r = at.measure_reference(m)
    if not r.ref:
        return MeasureMapping(m.uri, m.title, m.reference, "no_reference", note=r.basis)
    sections = list(dict.fromkeys(at._section_of(t) for t in r.provisions)) if r.provisions else []
    return MeasureMapping(m.uri, m.title, m.reference, "mapped", r.ref, sections=sections, note=r.basis,
                          abbreviation=r.abbreviation)


def map_measures(
    country: str,
    measures: Iterable[Any],
    *,
    celex: str | None = None,
    cellar: Any = None,
    client: Any = None,
) -> list[MeasureMapping]:
    """Map CELLAR measures (:class:`openacquis.cellar.NationalMeasure`) to adapter references.

    Germany needs ``celex`` and a CELLAR client (gazette numbers are queried separately) and
    looks acts up in NeuRIS through ``client``; the other countries map offline from the
    measure's reference and title.
    """
    a = get_adapter(country)
    measures = list(measures)
    if a.code == "de":
        numbers = de.journal_numbers(celex, "DEU", cellar) if celex else {}
        out = []
        for ms in measures:
            try:
                mm = de.map_measure(ms, client, *numbers.get(ms.uri, (None, None)))
                status = mm.status
                out.append(MeasureMapping(ms.uri, ms.title, ms.reference, status, mm.refs[0] if mm.refs else None,
                                          note=mm.note))
                for extra in mm.refs[1:]:  # one BGBl issue can re-enact several acts
                    out.append(MeasureMapping(ms.uri, ms.title, ms.reference, status, extra, note=mm.note))
            except (LookupError, ValueError, OSError) as exc:
                out.append(MeasureMapping(ms.uri, ms.title, ms.reference, "error", note=f"{type(exc).__name__}: {exc}"))
        return out
    fn: Callable[[Any], MeasureMapping] = {"cz": _cz_map, "sk": _sk_map, "at": _at_map}[a.code]
    return [fn(m) for m in measures]


__all__ = [
    "ADAPTERS",
    "Adapter",
    "ESbirkaClient",
    "GiiClient",
    "MeasureMapping",
    "RisClient",
    "SlovLexClient",
    "fetch_act",
    "fetch_measure",
    "get_adapter",
    "map_measures",
    "parse_fragments",
    "parse_reference",
]
