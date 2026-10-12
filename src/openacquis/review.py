"""Human review of machine suggestions: record format, validation and merging (v0.4).

Reviews are plain JSON lines in the repository, one file per directive and country::

    data/reviews/32022L2555_cz.jsonl

Each line is one reviewer's verdict on one machine suggestion::

    {"schema": 1,
     "suggestion_id": "link:eu:32022L2555:cs:art23.par4.pt_a>cz:264/2025:par16.odst1",
     "directive": "32022L2555", "country": "cz", "kind": "link",
     "eu_id": "eu:32022L2555:cs:art23.par4.pt_a", "national_id": "cz:264/2025:par16.odst1",
     "machine": {"label": "candidate", "score": 0.58, "rank": 1, "method": "hybrid:...", "generator": "openacquis 0.4.0"},
     "verdict": "confirm", "corrected_ids": [],
     "reviewer": "octocat", "date": "2026-10-12", "note": ""}

``kind`` is what was suggested:

* ``link``: the national provision implements the EU provision (``national_id`` set);
* ``gap``: nothing in the acts searched implements the EU provision (``national_id`` null);
* ``extra_rule``: the national provision implements nothing in the directive (``eu_id`` null).

``verdict``:

* ``confirm``: the suggestion is right;
* ``reject``: it is wrong (and the reviewer does not know the right answer);
* ``correct``: it is wrong and ``corrected_ids`` holds the right provisions: national IDs for
  a ``link`` or a ``gap``, EU IDs for an ``extra_rule``.

Records come from the website's review page (a prefilled GitHub issue, or a downloaded
JSONL file for a pull request). :func:`validate_record` checks one; :func:`merge` folds
all records into one decision per suggestion (the latest verdict of each reviewer; a
suggestion is ``disputed`` when reviewers disagree). Nothing here is a legal finding:
a confirmed link means one reviewer agreed with the pairing.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

SCHEMA = 1
KINDS = ("link", "gap", "extra_rule")
VERDICTS = ("confirm", "reject", "correct")
REVIEWS_DIR = Path(__file__).resolve().parents[2] / "data" / "reviews"

FIELDS = ("schema", "suggestion_id", "directive", "country", "kind", "eu_id", "national_id", "machine",
          "verdict", "corrected_ids", "reviewer", "date", "note")
MACHINE_FIELDS = ("label", "score", "rank", "method", "generator")

# Always used with ``fullmatch``: ``$`` would also accept a trailing newline ("cz\\n").
# The act part of a national ID is restricted to the characters the adapters produce
# (264/2025, 69/2018, 20013065, bsig_2025) so that a valid ID never carries markup.
_CELEX = re.compile(r"3\d{4}L\d{4}")
_COUNTRY = re.compile(r"[a-z]{2}")
_EU_ID = re.compile(r"eu:(3\d{4}L\d{4}):([a-z]{2}):([a-z0-9_]+(?:\.[a-z0-9_]+)*)")
_NAT_ID = re.compile(r"([a-z]{2}):([A-Za-z0-9_./-]{1,40}):([a-z0-9_]+(?:\.[a-z0-9_]+)*)")
_HANDLE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")  # GitHub user names
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SAFE_NAME = re.compile(r"[a-z_]{1,40}")  # field names echoed back in error messages
MAX_NOTE = 2000
MAX_CORRECTED = 20


class ReviewError(ValueError):
    """A review record that does not validate; ``errors`` lists every problem found."""

    def __init__(self, errors: Sequence[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


def suggestion_id(kind: str, eu_id: str | None, national_id: str | None) -> str:
    """Stable ID of a suggestion: ``link:<eu_id>><national_id>`` (``-`` for a missing side)."""
    return f"{kind}:{eu_id or '-'}>{national_id or '-'}"


def review_file(directive: str, country: str, root: Path | str | None = None) -> Path:
    """``data/reviews/<directive>_<cc>.jsonl``."""
    return Path(root or REVIEWS_DIR) / f"{directive.upper()}_{country.lower()}.jsonl"


def make_record(
    *,
    directive: str,
    country: str,
    kind: str,
    eu_id: str | None,
    national_id: str | None,
    verdict: str,
    reviewer: str,
    corrected_ids: Sequence[str] = (),
    machine: dict | None = None,
    note: str = "",
    day: str | None = None,
) -> dict:
    """A review record with its ``suggestion_id`` filled in, validated."""
    rec = {
        "schema": SCHEMA,
        "suggestion_id": suggestion_id(kind, eu_id, national_id),
        "directive": directive.upper(),
        "country": country.lower(),
        "kind": kind,
        "eu_id": eu_id,
        "national_id": national_id,
        "machine": dict(machine or {}),
        "verdict": verdict,
        "corrected_ids": list(corrected_ids),
        "reviewer": reviewer,
        "date": day or date.today().isoformat(),
        "note": note,
    }
    validate_record(rec)
    return rec


def _names(names: Sequence[str]) -> str:
    """Unknown field names for an error message: listed only when they are plain identifiers.

    Error messages end up in a bot comment on GitHub, so values chosen by the submitter
    (which could @mention people or carry links and images) are never echoed.
    """
    if all(_SAFE_NAME.fullmatch(n) for n in names):
        return ": " + ", ".join(names)
    return f" ({len(names)})"


def validate_record(rec: Any, known_ids: Iterable[str] | None = None) -> dict:
    """Check one record; return it, or raise :class:`ReviewError` listing every problem.

    ``known_ids`` (all provision IDs of the alignment the review is about) additionally
    checks that the IDs exist. Error messages quote no free text from the record (only
    values that passed their format check), so they are safe to post in a comment.
    """
    errors: list[str] = []
    if not isinstance(rec, dict):
        raise ReviewError(["a review record must be a JSON object"])
    unknown = sorted(set(rec) - set(FIELDS))
    if unknown:
        errors.append(f"unknown fields{_names(unknown)}")
    missing = [f for f in FIELDS if f not in rec]
    if missing:
        errors.append(f"missing fields: {', '.join(missing)}")
        raise ReviewError(errors)
    if type(rec["schema"]) is not int or rec["schema"] != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    directive, country = rec["directive"], rec["country"]
    if not isinstance(directive, str) or not _CELEX.fullmatch(directive):
        errors.append("directive must be a directive CELEX number such as 32022L2555")
    if not isinstance(country, str) or not _COUNTRY.fullmatch(country):
        errors.append("country must be a lower-case two-letter code such as cz")
    kind = rec["kind"]
    if kind not in KINDS:
        errors.append(f"kind must be one of {', '.join(KINDS)}")
    eu_id, nat_id = rec["eu_id"], rec["national_id"]
    directive_ok = isinstance(directive, str) and bool(_CELEX.fullmatch(directive))
    country_ok = isinstance(country, str) and bool(_COUNTRY.fullmatch(country))

    def check_eu(x: Any, what: str) -> bool:
        m = _EU_ID.fullmatch(x) if isinstance(x, str) else None
        if not m:
            errors.append(f"{what} must look like eu:32022L2555:cs:art23.par4.pt_a")
            return False
        if directive_ok and m.group(1) != directive:
            errors.append(f"{what} belongs to {m.group(1)}, not {directive}")
        return True

    def check_nat(x: Any, what: str) -> bool:
        m = _NAT_ID.fullmatch(x) if isinstance(x, str) else None
        if not m:
            errors.append(f"{what} must look like cz:264/2025:par16.odst1")
            return False
        if country_ok and m.group(1) != country:
            errors.append(f"{what} belongs to {m.group(1).upper()}, not {country.upper()}")
        return True

    ids_ok = kind in KINDS
    if kind in ("link", "gap"):
        ids_ok &= check_eu(eu_id, "eu_id")
    elif eu_id is not None:
        errors.append("eu_id must be null for an extra_rule suggestion")
        ids_ok = False
    if kind in ("link", "extra_rule"):
        ids_ok &= check_nat(nat_id, "national_id")
    elif nat_id is not None:
        errors.append("national_id must be null for a gap suggestion")
        ids_ok = False
    if kind in KINDS and rec["suggestion_id"] != suggestion_id(kind, eu_id, nat_id):
        # The expected value is quoted only when it is built from IDs that passed their check.
        errors.append(f"suggestion_id must be {suggestion_id(kind, eu_id, nat_id)!r}" if ids_ok
                      else "suggestion_id does not match kind, eu_id and national_id")

    machine = rec["machine"]
    if not isinstance(machine, dict):
        errors.append("machine must be an object")
    else:
        extra = sorted(set(machine) - set(MACHINE_FIELDS))
        if extra:
            errors.append(f"unknown machine fields{_names(extra)}")
        score = machine.get("score")
        if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float))
                                  or not math.isfinite(score)):
            errors.append("machine.score must be a number (finite, not NaN or Infinity)")
        for k in ("label", "method", "generator"):
            if machine.get(k) is not None and not isinstance(machine[k], str):
                errors.append(f"machine.{k} must be a string")
        rank = machine.get("rank")
        if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int) or rank < 1):
            errors.append("machine.rank must be a positive integer")

    verdict = rec["verdict"]
    if verdict not in VERDICTS:
        errors.append(f"verdict must be one of {', '.join(VERDICTS)}")
    corrected = rec["corrected_ids"]
    if not isinstance(corrected, list) or not all(isinstance(x, str) for x in corrected):
        errors.append("corrected_ids must be a list of provision IDs")
        corrected = []
    if verdict == "correct" and not corrected:
        errors.append("a 'correct' verdict needs corrected_ids")
    if verdict in ("confirm", "reject") and corrected:
        errors.append(f"corrected_ids must be empty for a {verdict!r} verdict")
    if len(corrected) > MAX_CORRECTED:
        errors.append(f"at most {MAX_CORRECTED} corrected_ids")
    for i, x in enumerate(corrected):
        (check_eu if kind == "extra_rule" else check_nat)(x, f"corrected_ids[{i}]")

    reviewer = rec["reviewer"]
    if not isinstance(reviewer, str) or not _HANDLE.fullmatch(reviewer):
        errors.append("reviewer must be a GitHub user name (letters, digits, hyphens)")
    day = rec["date"]
    if not isinstance(day, str) or not _DATE.fullmatch(day):
        errors.append("date must be YYYY-MM-DD")
    else:
        try:
            date.fromisoformat(day)
        except ValueError:
            errors.append("date is not a valid calendar date")
    note = rec["note"]
    if not isinstance(note, str):
        errors.append("note must be a string")
    elif len(note) > MAX_NOTE:
        errors.append(f"note is longer than {MAX_NOTE} characters")

    if known_ids is not None and not errors:
        known = set(known_ids)
        for x in [eu_id, nat_id, *corrected]:
            if x is not None and x not in known:
                errors.append(f"{x} is not a provision of this alignment")
    if errors:
        raise ReviewError(errors)
    return rec


# --- reading -----------------------------------------------------------------------------

_JSON_BLOCK = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.S)
_HEADING = re.compile(r"^### +(.+?)\s*$", re.M)
RECORD_HEADING = "Review record"   # .github/ISSUE_TEMPLATE/review.yml: the record textarea
LICENCE_HEADING = "Licence"        # ... and the CC-BY-4.0 checkbox
CONSENT = "I agree that this review is published under CC-BY-4.0"
_CONSENT = re.compile(r"^\s*- \[[xX]\] " + re.escape(CONSENT), re.M)


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _loads(text: str) -> Any:
    """``json.loads`` that rejects NaN/Infinity and turns deep nesting into ``ValueError``."""
    try:
        return json.loads(text, parse_constant=_no_constant)
    except RecursionError:
        raise ValueError("JSON nested too deeply") from None


def issue_section(text: str, heading: str) -> str | None:
    """The text under ``### <heading>`` in an issue-form body, up to the next ``### `` heading."""
    found = None
    for m in _HEADING.finditer(text):
        if found is not None:
            return text[found:m.start()]
        if m.group(1).strip().lower() == heading.lower():
            found = m.end()
    return text[found:] if found is not None else None


def records_from_text(text: str) -> list[dict]:
    """Records in a GitHub issue body or a JSONL file.

    An issue body holds one or more fenced ```json blocks (each a record, or a list of
    records); a file holds one JSON object per line. When the body is an issue form (it has
    a ``### Review record`` heading), only that section is read: the optional comment
    field is not part of the dataset. ``ValueError`` when nothing parses.
    """
    section = issue_section(text, RECORD_HEADING)
    if section is not None:
        text = section
    blocks = _JSON_BLOCK.findall(text)
    out: list[dict] = []
    if blocks:
        for b in blocks:
            try:
                data = _loads(b)
            except json.JSONDecodeError as exc:
                raise ValueError(f"not JSON ({exc.msg})") from None
            out.extend(data if isinstance(data, list) else [data])
        return out
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(_loads(line))
        except ValueError as exc:
            msg = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
            raise ValueError(f"line {n}: not JSON ({msg})") from None
    if not out:
        raise ValueError("no review record found (expected a ```json block or JSON lines)")
    return out


def check_issue(body: str, author: str) -> dict:
    """What the review bot reports for an issue body opened by ``author``.

    ``{"valid", "records", "errors"}``: every record validates, every record's
    ``reviewer`` is the issue author (case-insensitive; nobody can file a review, and
    consent to the licence, in someone else's name) and the licence box under
    ``### Licence`` is ticked. Errors quote nothing the submitter wrote.
    """
    errors: list[str] = []
    records: list[dict] = []
    try:
        found = records_from_text(body)
    except ValueError as exc:  # messages from records_from_text quote no input
        found = []
        errors.append(f"the review record could not be read: {exc}")
    for i, rec in enumerate(found, 1):
        try:
            validate_record(rec)
        except ReviewError as exc:
            errors.extend(f"record {i}: {e}" for e in exc.errors[:10])
            continue
        if rec["reviewer"].lower() != author.lower():
            errors.append(f"record {i}: reviewer must be the GitHub user who opened this issue")
        records.append(rec)
    if not found and not errors:
        errors.append("no review record found")
    licence = issue_section(body, LICENCE_HEADING)
    if licence is None or not _CONSENT.search(licence):
        errors.append("please tick the CC-BY-4.0 licence box")
    return {"valid": not errors and bool(records), "records": len(records), "errors": errors[:20]}


def _main(argv: Sequence[str] | None = None) -> int:
    """``python -m openacquis.review check-issue FILE AUTHOR``: the review bot's check, as JSON."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3 or args[0] != "check-issue":
        print("usage: python -m openacquis.review check-issue FILE AUTHOR", file=sys.stderr)
        return 2
    with open(args[1], encoding="utf-8", errors="replace") as f:
        result = check_issue(f.read(), args[2])
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["valid"] else 1


def load_reviews(root: Path | str | None = None) -> list[dict]:
    """Every record under ``data/reviews/`` (validated; ``ReviewError`` names file and line)."""
    out = []
    for path in sorted(Path(root or REVIEWS_DIR).glob("*.jsonl")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = validate_record(_loads(line))
            except ValueError as exc:  # ReviewError, JSONDecodeError, NaN, nesting
                raise ReviewError([f"{path.name}:{n}: {exc}"]) from None
            expected = review_file(rec["directive"], rec["country"], path.parent).name
            if path.name != expected:
                raise ReviewError([f"{path.name}:{n}: record for {rec['directive']} x {rec['country']} "
                                   f"belongs in {expected}"])
            out.append(rec)
    return out


def append_records(records: Sequence[dict], root: Path | str | None = None) -> list[Path]:
    """Validate and append records to their ``data/reviews/`` files; returns the files written.

    A record already present (same suggestion, reviewer, verdict, date and note) is skipped.
    """
    for r in records:
        validate_record(r)
    written: dict[Path, list[str]] = {}
    for r in records:
        path = review_file(r["directive"], r["country"], root)
        have = set()
        if path.exists():
            have = {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
        line = json.dumps({k: r[k] for k in FIELDS}, ensure_ascii=False, sort_keys=False, allow_nan=False)
        if line in have or line in written.get(path, []):
            continue
        written.setdefault(path, []).append(line)
    for path, lines in written.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
    return sorted(written)


# --- merging -------------------------------------------------------------------------------


@dataclass
class Decision:
    """All reviews of one suggestion folded together."""

    suggestion_id: str
    directive: str
    country: str
    kind: str
    eu_id: str | None
    national_id: str | None
    #: confirmed | rejected | corrected | disputed
    status: str
    verdicts: dict[str, str] = field(default_factory=dict)  # reviewer -> latest verdict
    corrected_ids: list[str] = field(default_factory=list)
    reviewers: list[str] = field(default_factory=list)
    last_date: str = ""
    notes: list[str] = field(default_factory=list)
    machine: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def links(self) -> list[tuple[str, str]]:
        """(EU ID, national ID) pairs this decision supports (none when rejected or disputed)."""
        if self.status == "confirmed" and self.kind == "link":
            return [(self.eu_id, self.national_id)]  # type: ignore[list-item]
        if self.status == "corrected":
            if self.kind in ("link", "gap"):
                return [(self.eu_id, n) for n in self.corrected_ids]  # type: ignore[misc]
            return [(e, self.national_id) for e in self.corrected_ids]  # type: ignore[misc]
        return []


_STATUS = {"confirm": "confirmed", "reject": "rejected", "correct": "corrected"}


def merge(records: Iterable[dict]) -> list[Decision]:
    """One :class:`Decision` per suggestion: each reviewer's latest record counts once.

    Same verdict from everyone -> that status (corrections must also agree on the IDs);
    otherwise ``disputed``. Order: as first seen.
    """
    latest: dict[str, dict[str, dict]] = {}
    order: list[str] = []
    for r in records:
        sid = r["suggestion_id"]
        if sid not in latest:
            latest[sid] = {}
            order.append(sid)
        prev = latest[sid].get(r["reviewer"])
        if prev is None or r["date"] >= prev["date"]:
            latest[sid][r["reviewer"]] = r
    out = []
    for sid in order:
        recs = list(latest[sid].values())
        first = recs[0]
        verdicts = {r["reviewer"]: r["verdict"] for r in recs}
        kinds = {r["verdict"] for r in recs}
        corrections = {tuple(sorted(r["corrected_ids"])) for r in recs if r["verdict"] == "correct"}
        if len(kinds) == 1 and len(corrections) <= 1:
            status = _STATUS[first["verdict"]]
        else:
            status = "disputed"
        out.append(Decision(
            suggestion_id=sid, directive=first["directive"], country=first["country"], kind=first["kind"],
            eu_id=first["eu_id"], national_id=first["national_id"], status=status, verdicts=verdicts,
            corrected_ids=list(next(iter(corrections))) if status == "corrected" else [],
            reviewers=sorted(verdicts), last_date=max(r["date"] for r in recs),
            notes=[r["note"] for r in recs if r["note"]], machine=first.get("machine") or {},
        ))
    return out


def reviewed_ids(records: Iterable[dict]) -> set[str]:
    """Suggestion IDs that have at least one review (the review page hides them)."""
    return {r["suggestion_id"] for r in records}


if __name__ == "__main__":
    raise SystemExit(_main())
