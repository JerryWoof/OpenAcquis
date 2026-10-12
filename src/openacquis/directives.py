"""Directive registry (v1.0): which directives OpenAcquis aligns, and with which national acts.

The registry lives in ``data/directives.json``. Per directive it records the CELEX number,
a short name, a topic, the transposition deadline(s) and, per country with an adapter
(``cz``, ``sk``, ``at``, ``de``), the **main transposing act(s)** as adapter references::

    from openacquis.directives import load_registry, resolve_main_acts

    reg = load_registry()
    nis2 = reg.get("32022L2555")
    resolve_main_acts(nis2, "at").acts          # ['20013065'] (NISG 2026)

Country entries
    ``status``   ``ok`` (a notified transposition), ``baseline`` (the act was *not* notified
                 for this directive: it is aligned as the existing national law, e.g. Germany's
                 EntgTranspG for Pay Transparency) or ``none`` (nothing to align; ``note`` says why).
    ``acts``     adapter references, first = main act (only its units can be extra-rule
                 candidates, see :func:`openacquis.align.align`).
    ``override`` ``true`` when the acts were set by hand because the adapter mapping of CELLAR's
                 notified measures (:func:`openacquis.adapters.map_measures`) does not find them
                 (German omnibus laws mapped ``not_found``, Austrian amending acts ...).
    ``aliases``  references the mapping produces for these acts when they differ from ``acts``
                 (Austria: ``NISG 2026`` for Gesetzesnummer 20013065; Slovakia's ``69/2018-366/2024``
                 matches ``69/2018`` without an alias).
    ``label``, ``version`` (consolidated version, as in :data:`openacquis.align.DEFAULT_VERSIONS`),
    ``max_sections`` (Austria: cap on § documents fetched, one request each), ``note``.
    ``act_labels`` how secondary acts are cited on the pages (``{"urhg": "UrhG"}``; the main act's
                 is ``label``).
    ``public_note`` the note as the website shows it, when ``note`` is a developer note (mapper
                 statuses such as ``not_found``); without it the site shows the public clauses of
                 ``note`` (``scripts/build_site.py`` ``public_note``).
    ``extra_rules`` ``false`` when the main act is a general code (a Labour Code, AktG ...) of which
                 the directive concerns only a part: no extra-rule candidates are reported, as
                 most of such an act matches nothing in the directive. ``baseline`` entries never
                 report them (:attr:`CountryEntry.reports_extra_rules`).

Resolution (:func:`resolve_main_acts`): an override wins; otherwise, given CELLAR's mapped
measures, the registry acts found among them (else the best-looking main act among them);
otherwise the acts as recorded. Stdlib only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO_ROOT / "data" / "directives.json"
SCHEMA = 1
COUNTRIES = ("cz", "sk", "at", "de")
STATUSES = ("ok", "baseline", "none")

_CELEX = re.compile(r"^3\d{4}L\d{4}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DIRECTIVE_KEYS = {"celex", "short", "topic", "rank", "deadline", "deadlines", "countries", "title", "note"}
_COUNTRY_KEYS = {"status", "acts", "label", "version", "override", "aliases", "note", "max_sections", "extra_rules",
                 "act_labels", "public_note"}


class RegistryError(ValueError):
    """The registry file is malformed; the message lists every problem found."""


@dataclass(frozen=True)
class CountryEntry:
    """One directive x one country in the registry."""

    country: str
    status: str
    acts: tuple[str, ...] = ()
    label: str = ""
    version: str | None = None
    override: bool = False
    aliases: tuple[str, ...] = ()
    note: str = ""
    max_sections: int | None = None
    extra_rules: bool = True
    act_labels: tuple[tuple[str, str], ...] = ()
    public_note: str = ""

    @property
    def main(self) -> str | None:
        return self.acts[0] if self.acts else None

    @property
    def alignable(self) -> bool:
        return self.status != "none" and bool(self.acts)

    @property
    def reports_extra_rules(self) -> bool:
        """Extra-rule candidates are meaningful: a notified act (not a baseline) that is not a general code."""
        return self.extra_rules and self.status == "ok"

    def act_label(self, ref: str) -> str:
        """How ``ref`` is cited on the pages: ``label`` for the main act, ``act_labels`` for the others, else ``""``."""
        if ref == self.main and self.label:
            return self.label
        return dict(self.act_labels).get(ref, "")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["acts"], d["aliases"] = list(self.acts), list(self.aliases)
        d["act_labels"] = dict(self.act_labels)
        return d


@dataclass(frozen=True)
class DirectiveEntry:
    """One directive in the registry."""

    celex: str
    short: str
    topic: str
    deadline: str | None = None
    deadlines: tuple[str, ...] = ()
    rank: int | None = None
    title: str | None = None
    note: str = ""
    countries: dict[str, CountryEntry] = field(default_factory=dict)

    def country(self, code: str) -> CountryEntry:
        """The entry for ``code`` (``cz`` / ``CZ``); a ``none`` entry when the registry has none."""
        c = code.strip().lower()
        return self.countries.get(c) or CountryEntry(c, "none", note=f"{c.upper()}: not in the registry")

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if k != "countries"}
        d["deadlines"] = list(self.deadlines)
        d["countries"] = {c: e.to_dict() for c, e in self.countries.items()}
        return d


@dataclass(frozen=True)
class Registry:
    directives: tuple[DirectiveEntry, ...]
    updated: str | None = None
    path: str | None = None

    def __iter__(self) -> Iterator[DirectiveEntry]:
        return iter(self.directives)

    def __len__(self) -> int:
        return len(self.directives)

    def get(self, celex: str) -> DirectiveEntry:
        c = celex.strip().upper()
        for d in self.directives:
            if d.celex == c:
                return d
        raise KeyError(f"{celex} is not in the directive registry ({', '.join(x.celex for x in self.directives)})")

    def celexes(self) -> list[str]:
        return [d.celex for d in self.directives]


# --- loading and validation -----------------------------------------------------------


def validate(data: Any) -> list[str]:
    """Every problem in raw registry data (``[]`` = valid)."""
    if not isinstance(data, dict):
        return ["registry must be a JSON object"]
    problems: list[str] = []
    if data.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA}, got {data.get('schema')!r}")
    ds = data.get("directives")
    if not isinstance(ds, list) or not ds:
        return problems + ["'directives' must be a non-empty list"]
    seen: set[str] = set()
    for i, d in enumerate(ds):
        where = f"directives[{i}]"
        if not isinstance(d, dict):
            problems.append(f"{where}: must be an object")
            continue
        celex = d.get("celex")
        if not isinstance(celex, str) or not _CELEX.match(celex):
            problems.append(f"{where}: celex {celex!r} is not a directive CELEX number (e.g. 32022L2555)")
        else:
            where = celex
            if celex in seen:
                problems.append(f"{celex}: listed twice")
            seen.add(celex)
        for k in sorted(set(d) - _DIRECTIVE_KEYS):
            problems.append(f"{where}: unknown key {k!r}")
        for k in ("short", "topic"):
            if not isinstance(d.get(k), str) or not d[k].strip():
                problems.append(f"{where}: {k!r} must be a non-empty string")
        dl = d.get("deadline")
        if dl is not None and not (isinstance(dl, str) and _DATE.match(dl)):
            problems.append(f"{where}: deadline {dl!r} must be YYYY-MM-DD")
        dls = d.get("deadlines", [])
        if not isinstance(dls, list) or any(not (isinstance(x, str) and _DATE.match(x)) for x in dls):
            problems.append(f"{where}: deadlines must be a list of YYYY-MM-DD dates")
        if "rank" in d and not (isinstance(d["rank"], int) and d["rank"] > 0):
            problems.append(f"{where}: rank must be a positive integer")
        cs = d.get("countries")
        if not isinstance(cs, dict):
            problems.append(f"{where}: 'countries' must be an object keyed by country code")
            continue
        for cc, e in cs.items():
            problems.extend(_validate_country(f"{where} {cc}", cc, e))
    return problems


def _validate_country(where: str, cc: str, e: Any) -> list[str]:
    if cc not in COUNTRIES:
        return [f"{where}: no adapter for country {cc!r} (expected one of {', '.join(COUNTRIES)})"]
    if not isinstance(e, dict):
        return [f"{where}: must be an object"]
    out = [f"{where}: unknown key {k!r}" for k in sorted(set(e) - _COUNTRY_KEYS)]
    status = e.get("status")
    if status not in STATUSES:
        out.append(f"{where}: status {status!r} must be one of {', '.join(STATUSES)}")
    acts = e.get("acts", [])
    if not isinstance(acts, list) or any(not isinstance(a, str) or not a.strip() for a in acts):
        out.append(f"{where}: acts must be a list of adapter references")
    elif status in ("ok", "baseline") and not acts:
        out.append(f"{where}: status {status!r} needs at least one act")
    elif status == "none":
        if acts:
            out.append(f"{where}: status 'none' must have no acts")
        if not str(e.get("note", "")).strip():
            out.append(f"{where}: status 'none' needs a note saying why")
    elif len(set(acts)) != len(acts):
        out.append(f"{where}: an act is listed twice")
    if status == "baseline" and not str(e.get("note", "")).strip():
        out.append(f"{where}: status 'baseline' needs a note (what is aligned and why)")
    v = e.get("version")
    if v is not None and not (isinstance(v, str) and _DATE.match(v)):
        out.append(f"{where}: version {v!r} must be YYYY-MM-DD")
    if "override" in e and not isinstance(e["override"], bool):
        out.append(f"{where}: override must be true or false")
    if "extra_rules" in e and not isinstance(e["extra_rules"], bool):
        out.append(f"{where}: extra_rules must be true or false")
    al_ = e.get("act_labels", {})
    if not isinstance(al_, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip()
                                        for k, v in al_.items()):
        out.append(f"{where}: act_labels must map act references to non-empty labels")
    elif isinstance(acts, list) and any(k not in acts for k in al_):
        out.append(f"{where}: act_labels names an act that is not in acts")
    al = e.get("aliases", [])
    if not isinstance(al, list) or any(not isinstance(a, str) for a in al):
        out.append(f"{where}: aliases must be a list of strings")
    ms = e.get("max_sections")
    if ms is not None and not (isinstance(ms, int) and not isinstance(ms, bool) and ms > 0):
        out.append(f"{where}: max_sections must be a positive integer")
    for k in ("label", "note", "public_note"):
        if k in e and not isinstance(e[k], str):
            out.append(f"{where}: {k} must be a string")
    return out


def parse_registry(data: Any, path: str | None = None) -> Registry:
    """Validate raw registry data and build a :class:`Registry` (``RegistryError`` if invalid)."""
    problems = validate(data)
    if problems:
        raise RegistryError(f"invalid directive registry{f' {path}' if path else ''}:\n  " + "\n  ".join(problems))
    out = []
    for d in data["directives"]:
        countries = {
            cc: CountryEntry(cc, e["status"], tuple(e.get("acts", [])), e.get("label", ""), e.get("version"),
                             bool(e.get("override", False)), tuple(e.get("aliases", [])), e.get("note", ""),
                             e.get("max_sections"), bool(e.get("extra_rules", True)),
                             tuple(sorted(e.get("act_labels", {}).items())), e.get("public_note", ""))
            for cc, e in d["countries"].items()
        }
        out.append(DirectiveEntry(d["celex"], d["short"], d["topic"], d.get("deadline"),
                                  tuple(d.get("deadlines", [])), d.get("rank"), d.get("title"), d.get("note", ""),
                                  countries))
    return Registry(tuple(out), data.get("updated"), path)


def load_registry(path: str | Path | None = None) -> Registry:
    """Load and validate ``data/directives.json`` (or ``path``)."""
    p = Path(path) if path else REGISTRY_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryError(f"{p}: not valid JSON ({exc})") from exc
    return parse_registry(data, str(p))


# --- main act resolution --------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    """The national acts to align for one directive x country, and where they came from.

    ``source``: ``override`` (set by hand in the registry), ``cellar`` (found among CELLAR's
    notified measures as the adapter maps them), ``cellar-heuristic`` (no registry act among
    them; picked by :func:`pick_main_act`), ``registry`` (as recorded; no mapping consulted)
    or ``none`` (nothing to align).
    """

    celex: str
    country: str
    acts: tuple[str, ...]
    source: str
    status: str
    label: str = ""
    version: str | None = None
    note: str = ""
    max_sections: int | None = None
    extra_rules: bool = True  # report extra-rule candidates (CountryEntry.reports_extra_rules)
    act_labels: tuple[tuple[str, str], ...] = ()

    @property
    def main(self) -> str | None:
        return self.acts[0] if self.acts else None

    def act_label(self, ref: str) -> str:
        """``label`` for the main act, ``act_labels`` for the others, else ``""``."""
        if ref == self.main and self.label:
            return self.label
        return dict(self.act_labels).get(ref, "")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["acts"] = list(self.acts)
        d["act_labels"] = dict(self.act_labels)
        return d


def base_ref(ref: str) -> str:
    """Comparable form of an adapter reference: ``69/2018-366/2024`` -> ``69/2018``; case and spaces folded."""
    r = re.sub(r"\s+", " ", ref.strip()).lower()
    m = re.match(r"^(\d+[a-z]?/\d{4})-\d+[a-z]?/\d{4}$", r)
    return m.group(1) if m else r


_CZ_MAIN = re.compile(r"^\s*Zákon\s+č\.\s*\d+[a-z]?/\d{4}\s+Sb\.,?\s+o\s", re.IGNORECASE)
_SK_MAIN = re.compile(r"^\s*Zákon\s+č\.\s*\d+[a-z]?/\d{4}\s+Z\.\s*z\.,?\s+o\s", re.IGNORECASE)
_AMENDING = re.compile(r"kterým\s+se\s+mění|ktorým\s+sa\s+mení|ändert|Änderung|Novelle", re.IGNORECASE)


def pick_main_act(country: str, mappings: Sequence[Any]) -> str | None:
    """A best guess at the main transposing act among mapped measures (``None`` if none is mapped).

    Czech and Slovak titles: the first ``Zákon č. N/YYYY Sb., o …`` that is not an amending act
    (CZ notifies many loosely related laws, so the count of measures says little); otherwise the
    first mapped measure that is not an amending act; otherwise the first mapped one.
    """
    mapped = [m for m in mappings if getattr(m, "status", None) == "mapped" and getattr(m, "ref", None)]
    if not mapped:
        return None
    pattern = {"cz": _CZ_MAIN, "sk": _SK_MAIN}.get(country.lower())
    if pattern is not None:
        for m in mapped:
            t = m.title or ""
            if pattern.match(t) and not _AMENDING.search(t):
                return m.ref
    for m in mapped:
        if not _AMENDING.search(m.title or ""):
            return m.ref
    return mapped[0].ref


def resolve_main_acts(
    entry: DirectiveEntry,
    country: str,
    mappings: Iterable[Any] | None = None,
) -> Resolution:
    """The acts to align for ``entry`` in ``country``: override > adapter mapping > registry.

    ``mappings`` are :class:`openacquis.adapters.MeasureMapping` items for this directive and
    country (``openacquis.align.notified_measures``); without them the registry acts are used
    as recorded. With them, the registry acts (or their ``aliases``) the mapping produced are
    returned in registry order (a Slovak ``69/2018-366/2024`` counts as ``69/2018``); if it
    produced none of them, :func:`pick_main_act` guesses.
    """
    e = entry.country(country)
    common = dict(celex=entry.celex, country=e.country, status=e.status, label=e.label, version=e.version,
                  note=e.note, max_sections=e.max_sections, extra_rules=e.reports_extra_rules,
                  act_labels=e.act_labels)
    if not e.alignable:
        return Resolution(acts=(), source="none", **common)
    if e.override:
        return Resolution(acts=e.acts, source="override", **common)
    if mappings is None:
        return Resolution(acts=e.acts, source="registry", **common)
    mapped = [m for m in mappings if getattr(m, "status", None) == "mapped" and getattr(m, "ref", None)]
    found: dict[str, str] = {}
    for act in e.acts:
        keys = {base_ref(act)} | ({base_ref(a) for a in e.aliases} if act == e.main else set())
        for m in mapped:
            if base_ref(m.ref) in keys:
                found[act] = act  # the registry reference (fetchable as is) wins over the alias
                break
    if found:
        return Resolution(acts=tuple(a for a in e.acts if a in found), source="cellar", **common)
    guess = pick_main_act(e.country, mapped)
    if guess is None:
        return Resolution(acts=e.acts, source="registry",
                          **{**common, "note": (e.note + "; " if e.note else "") + "no measure mapped in CELLAR"})
    return Resolution(acts=(guess,), source="cellar-heuristic",
                      **{**common, "note": (e.note + "; " if e.note else "")
                         + f"registry acts not among CELLAR's mapped measures; guessed {guess}"})


def alignment_jobs(
    registry: Registry,
    celexes: Iterable[str] | None = None,
    countries: Iterable[str] | None = None,
) -> list[tuple[DirectiveEntry, str]]:
    """Every (directive, country) pair of the registry, in registry order (``none`` entries included)."""
    want_c = {c.upper() for c in celexes} if celexes else None
    want_cc = [c.lower() for c in countries] if countries else list(COUNTRIES)
    return [(d, cc) for d in registry if want_c is None or d.celex in want_c for cc in want_cc]


__all__ = [
    "COUNTRIES",
    "REGISTRY_PATH",
    "STATUSES",
    "CountryEntry",
    "DirectiveEntry",
    "Registry",
    "RegistryError",
    "Resolution",
    "alignment_jobs",
    "base_ref",
    "load_registry",
    "parse_registry",
    "pick_main_act",
    "resolve_main_acts",
    "validate",
]
