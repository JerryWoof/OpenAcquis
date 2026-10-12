"""Judge every candidate pair of one directive x country through the Message Batches API.

Input is an alignment JSON, either the website's page data (``site/static-data/<celex>_<cc>.json``,
one row per directive paragraph with its top machine candidates) or the output of
``openacquis align --out`` (one row per EU unit with ranked candidates). The top ``k`` candidates of
every row become pairs; each pair is judged by :class:`openacquis.judge.AnthropicJudge` with the
prompt for that country, and the answer goes through the same evidence checks as an interactive
call (:func:`openacquis.judge.verify_evidence`).

A run is **resumable**. A small state file (default
``$OPENACQUIS_CACHE/judge-batches/<celex>_<cc>_<model>.json``) records the batches submitted and the
outcome of every pair; results are stored in the judge's disk cache (``$OPENACQUIS_CACHE/judge``) as
they are collected. Running the same command again collects batches that have ended, re-submits
pairs whose requests expired or failed on the server side (up to ``--max-attempts``), and submits
nothing that already has a result. Batches can take up to 24 hours; ``--wait`` limits how long one
run polls.

Money: ``--dry-run`` prints pair counts and the cost estimate and never needs a key or the SDK.
``--max-cost`` (USD) refuses to submit when the money already spent in this state, plus the
worst case for batches still running, plus the worst case for the new requests (no prompt cache hits
and every answer at ``--max-tokens``) would exceed it, so the bill stays under the cap. The typical
cost (cache hits, measured or assumed output length) is printed next to it. Prices are in :data:`openacquis.judge.PRICES`.

Output: ``data/judgments/<celex>_<cc>.jsonl``, one :class:`~openacquis.judge.Judgment` per line
(plus ``celex``, ``country``, ``candidate_rank``, ``candidate_score``) in alignment order. Every
line is a machine suggestion (``status = "unreviewed"``), never a finding that a country is in
breach of EU law.

Command line::

    python -m openacquis.judge_batch run site/static-data/32022L2555_cz.json --dry-run
    python -m openacquis.judge_batch run site/static-data/32022L2555_cz.json --max-cost 10 --wait 3600
    python -m openacquis.judge_batch status site/static-data/32022L2555_cz.json

Exit codes: 0 every pair has a result or a final failure; 2 bad input or an interrupted submission;
3 refused by ``--max-cost``; 4 batches still running or pairs still to submit (run again later).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .judge import (
    DEFAULT_MODEL,
    PRICES,
    PROMPT_COUNTRIES,
    USAGE_KEYS,
    AnthropicJudge,
    Judgment,
    Passage,
    build_user_message,
    estimate_cost,
    price_usage,
)

STATE_SCHEMA = "openacquis-judge-batch/1"

#: Per-batch limits we stay under (the API allows 100,000 requests or 256 MB per batch).
MAX_REQUESTS_PER_BATCH = 50_000
MAX_BYTES_PER_BATCH = 200 * 1024 * 1024

#: Rough characters per token, for estimates without a key. Czech and Slovak legal text tokenises
#: densely; measured NIS2 pairs (about 870-1,030 characters) are about 300-400 tokens.
CHARS_PER_TOKEN: dict[str, float] = {"cs": 2.5, "sk": 2.5, "de": 3.0, "en": 4.0}
DEFAULT_CHARS_PER_TOKEN = 3.0
#: Assumed output per pair (JSON answer plus low-effort thinking) until real results are in.
DEFAULT_OUTPUT_TOKENS = 450

#: Directive language expected for each country's prompt.
PROMPT_LANGUAGE = {"cz": "cs", "sk": "sk", "at": "de", "de": "de"}

#: Outcomes that are worth sending again in a later batch.
RETRYABLE = ("expired", "canceled")
#: Outcomes that end a pair in batch mode (``--retry-interactive`` sends refusals and invalid output
#: once through the standard API, which has the server-side refusal fallback).
FINAL = ("errored:invalid_request", "refusal", "truncated", "invalid_output")

EXIT_OK, EXIT_INPUT, EXIT_BUDGET, EXIT_PENDING = 0, 2, 3, 4

_CELEX = re.compile(r"^[0-9]{5}[A-Z]{1,2}[0-9]{4}$")


class BatchError(RuntimeError):
    """Bad input, an interrupted submission or a state file that does not fit the run."""


class BudgetExceeded(BatchError):
    """Submitting would exceed ``--max-cost``."""


# --- workload --------------------------------------------------------------------


@dataclass
class Pair:
    eu: Passage
    national: Passage
    rank: int
    score: float | None
    clipped: list[str] = field(default_factory=list)  # "eu" / "national": text was cut in the alignment JSON


@dataclass
class Workload:
    celex: str
    country: str  # two-letter, lower case
    language: str  # language of the directive text
    source: str
    k: int
    pairs: list[Pair]
    skipped: dict[str, int] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return f"{self.celex}_{self.country}"


def _clipped(text: str) -> bool:
    return text.rstrip().endswith("…")


def load_workload(path: Path | str, k: int = 3, country: str | None = None) -> Workload:
    """Candidate pairs (top ``k`` per row) from an alignment JSON (website page data or ``align --out``)."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BatchError(f"cannot read alignment JSON {path}: {e}") from e
    if k < 1:
        raise BatchError("k must be at least 1")
    if "articles" in data:
        wl = _from_page(data, k)
    elif "provisions" in data:
        wl = _from_align(data, k)
    else:
        raise BatchError(f"{path} is not an alignment JSON (no 'articles' or 'provisions')")
    wl.source = str(path)
    if country:
        cc = country.lower()
        if wl.country and wl.country != cc:
            raise BatchError(f"{path} is for country {wl.country!r}, not {cc!r}")
        wl.country = cc
    if wl.country not in PROMPT_COUNTRIES:
        raise BatchError(f"no judge prompt for country {wl.country!r}; expected one of {', '.join(PROMPT_COUNTRIES)}")
    return wl


def _nat_passage(texts: dict, nid: str, skipped: dict[str, int]) -> Passage | None:
    t = texts.get(nid) or {}
    text = (t.get("text") or "").strip()
    if not text:
        skipped["no_national_text"] = skipped.get("no_national_text", 0) + 1
        return None
    return Passage(nid, text)


def _from_page(data: dict, k: int) -> Workload:
    skipped: dict[str, int] = {}
    texts = data.get("national_texts") or {}
    pairs: list[Pair] = []
    for art in data.get("articles") or []:
        ctx = " ".join(x for x in (art.get("label"), art.get("heading")) if x) or None
        for row in art.get("rows") or []:
            eu_text = (row.get("text") or "").strip()
            machine = (row.get("machine") or [])[:k]
            if not eu_text:
                skipped["no_eu_text"] = skipped.get("no_eu_text", 0) + len(machine)
                continue
            eu = Passage(row["id"], eu_text, ctx)
            for rank, m in enumerate(machine, 1):
                nat = _nat_passage(texts, m["id"], skipped)
                if nat is not None:
                    pairs.append(_pair(eu, nat, rank, m.get("score")))
    eu_meta = data.get("eu") or {}
    return Workload(celex=str(data.get("celex") or ""), country=str(data.get("country_code") or "").lower(),
                    language=str(eu_meta.get("language") or data.get("national_language") or ""),
                    source="", k=k, pairs=pairs, skipped=skipped)


def _from_align(data: dict, k: int) -> Workload:
    skipped: dict[str, int] = {}
    texts = data.get("national_texts") or {}
    pairs: list[Pair] = []
    for row in data.get("provisions") or []:
        eu_text = (row.get("eu_text") or "").strip()
        cands = sorted(row.get("candidates") or [], key=lambda c: c.get("rank", 0))[:k]
        if not eu_text:
            skipped["no_eu_text"] = skipped.get("no_eu_text", 0) + len(cands)
            continue
        eu = Passage(row["eu_id"], eu_text, row.get("eu_label") or None)
        for i, c in enumerate(cands, 1):
            nat = _nat_passage(texts, c["national_id"], skipped)
            if nat is not None:
                pairs.append(_pair(eu, nat, int(c.get("rank") or i), c.get("score")))
    eu_meta = data.get("eu") or {}
    celex = str(eu_meta.get("celex") or eu_meta.get("act_id") or "")
    if not celex and pairs:
        celex = pairs[0].eu.id.split(":")[1]
    country = pairs[0].national.id.split(":", 1)[0] if pairs else ""
    return Workload(celex=celex, country=country.lower(), language=str(eu_meta.get("language") or ""),
                    source="", k=k, pairs=pairs, skipped=skipped)


def _pair(eu: Passage, nat: Passage, rank: int, score: Any) -> Pair:
    clipped = [side for side, p in (("eu", eu), ("national", nat)) if _clipped(p.text)]
    return Pair(eu, nat, rank, float(score) if isinstance(score, (int, float)) else None, clipped)


# --- estimates -----------------------------------------------------------------------


def estimate_tokens(text: str, language: str = "") -> int:
    """Rough token count without a key (see :data:`CHARS_PER_TOKEN`)."""
    return math.ceil(len(text) / CHARS_PER_TOKEN.get(language, DEFAULT_CHARS_PER_TOKEN))


def pair_tokens(pair: Pair, language: str, scale: float = 1.0) -> float:
    return estimate_tokens(build_user_message(pair.eu, pair.national), language) * scale


def system_tokens(judge: AnthropicJudge, scale: float = 1.0) -> float:
    return estimate_tokens(judge.system_prompt, "en") * scale  # the prompt is English with a few quoted phrases


def batch_cost(pairs: Sequence[Pair], judge: AnthropicJudge, language: str, *, output_tokens: float,
               cache_hit: float, scale: float = 1.0, batch: bool = True) -> float:
    """USD for these pairs at batch (or standard) prices; ``cache_hit=0`` is the upper bound."""
    if not pairs:
        return 0.0
    mean_in = sum(pair_tokens(p, language, scale) for p in pairs) / len(pairs)
    return estimate_cost(len(pairs), judge.model, system_tokens=round(system_tokens(judge, scale)),
                         pair_tokens=mean_in, output_tokens=output_tokens, batch=batch, cache_hit=cache_hit)


def measure_scale(judge: AnthropicJudge, pairs: Sequence[Pair], language: str, sample: int = 20) -> float:
    """Ratio of real input tokens (``messages.count_tokens``, free, needs a key) to the estimate."""
    if not pairs or sample <= 0:
        return 1.0
    step = max(1, len(pairs) // sample)
    chosen = list(pairs)[::step][:sample]
    real = est = 0.0
    for p in chosen:
        params = judge.request_params(p.eu, p.national)
        r = judge.client.messages.count_tokens(model=params["model"], system=params["system"],
                                               messages=params["messages"])
        real += float(getattr(r, "input_tokens", 0) or 0)
        est += pair_tokens(p, language) + system_tokens(judge)
    return real / est if est else 1.0


# --- state ---------------------------------------------------------------------------


def cache_root() -> Path:
    root = os.environ.get("OPENACQUIS_CACHE")
    return Path(root).expanduser() if root else Path.home() / ".cache" / "openacquis"


def default_state_path(celex: str, country: str, model: str) -> Path:
    return cache_root() / "judge-batches" / f"{celex}_{country}_{model}.json"


def default_output_path(celex: str, country: str, root: Path | str = "data/judgments") -> Path:
    return Path(root) / f"{celex}_{country}.jsonl"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_state(wl: Workload, judge: AnthropicJudge) -> dict:
    return {
        "schema": STATE_SCHEMA, "celex": wl.celex, "country": wl.country, "model": judge.model,
        "prompt_version": judge.prompt_version, "effort": judge.effort, "max_tokens": judge.max_tokens,
        "created": _now(), "updated": _now(), "batches": [], "pairs": {},
        "usage": dict.fromkeys(USAGE_KEYS, 0), "results": 0, "interactive_usage": dict.fromkeys(USAGE_KEYS, 0),
        "submitting": None,
    }


def load_state(path: Path, wl: Workload, judge: AnthropicJudge) -> dict:
    if not path.exists():
        return new_state(wl, judge)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise BatchError(f"state file {path} is not JSON: {e}") from e
    if state.get("schema") != STATE_SCHEMA:
        raise BatchError(f"state file {path} has schema {state.get('schema')!r}, expected {STATE_SCHEMA}")
    for k, mine in (("celex", wl.celex), ("country", wl.country), ("model", judge.model)):
        if state.get(k) != mine:
            raise BatchError(f"state file {path} is for {k}={state.get(k)!r}, this run has {mine!r}")
    for k, mine in (("prompt_version", judge.prompt_version), ("effort", judge.effort),
                    ("max_tokens", judge.max_tokens)):
        if state.get(k) != mine and any(not b.get("collected") for b in state.get("batches", [])):
            raise BatchError(f"state file {path} has running batches with {k}={state.get(k)!r}; this run has "
                             f"{mine!r}. Collect them first with the old settings, or use another --state.")
        state[k] = mine  # results are keyed by these settings in the disk cache, so old ones are not reused
    return state


def save_state(path: Path, state: dict) -> None:
    state["updated"] = _now()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _spent(state: dict, model: str) -> float:
    return (price_usage(state.get("usage", {}), model, batch=True)
            + price_usage(state.get("interactive_usage", {}), model))


def _measured_output(state: dict) -> float | None:
    n = int(state.get("results") or 0)
    return state["usage"]["output_tokens"] / n if n >= 20 else None


def worst_cost(pairs: Sequence[Pair], judge: AnthropicJudge, language: str, *, scale: float = 1.0,
               batch: bool = True) -> float:
    """True upper bound in USD: no prompt-cache hits and every answer at ``max_tokens``."""
    return batch_cost(pairs, judge, language, output_tokens=float(judge.max_tokens), cache_hit=0.0, scale=scale,
                      batch=batch)


# --- run -----------------------------------------------------------------------------


@dataclass
class Plan:
    """What a run would do: unique pairs by state."""

    keys: list[str]  # cache key per pair, in workload order
    first: dict[str, int]  # key -> index of its first pair
    cached: set[str]
    pending: set[str]  # in a batch that has not been collected
    failed: dict[str, str]  # key -> final outcome
    todo: list[str]  # to submit, in workload order


def make_plan(wl: Workload, judge: AnthropicJudge, state: dict, max_attempts: int) -> Plan:
    keys = [judge.cache_key(p.eu, p.national) for p in wl.pairs]
    first: dict[str, int] = {}
    for i, key in enumerate(keys):
        first.setdefault(key, i)
    cached = {key for key in first if judge.cached(key)}
    pending = {key for b in state["batches"] if not b.get("collected") for key in b["custom_ids"]}
    failed: dict[str, str] = {}
    todo: list[str] = []
    for key in first:
        if key in cached or key in pending:
            continue
        info = state["pairs"].get(key) or {}
        outcome = info.get("outcome")
        if outcome in FINAL:
            failed[key] = outcome
        elif outcome is not None and int(info.get("attempts", 0)) >= max_attempts:
            failed[key] = f"{outcome} (after {info.get('attempts')} attempts)"
        else:
            todo.append(key)
    return Plan(keys, first, cached, pending, failed, todo)


def _chunks(requests: list[dict]) -> list[list[dict]]:
    out: list[list[dict]] = []
    cur: list[dict] = []
    size = 0
    for r in requests:
        n = len(json.dumps(r, ensure_ascii=False).encode("utf-8"))
        if cur and (len(cur) >= MAX_REQUESTS_PER_BATCH or size + n > MAX_BYTES_PER_BATCH):
            out.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += n
    if cur:
        out.append(cur)
    return out


def run(
    alignment: Path | str,
    *,
    k: int = 3,
    country: str | None = None,
    model: str = DEFAULT_MODEL,
    effort: str | None = "low",
    max_tokens: int = 4096,
    client: Any = None,
    cache_dir: Path | str | None = None,
    state_path: Path | str | None = None,
    out: Path | str | None = None,
    dry_run: bool = False,
    max_cost: float | None = None,
    wait: float = 0.0,
    poll_interval: float = 60.0,
    max_attempts: int = 3,
    retry_interactive: bool = False,
    submit: bool = True,
    output_tokens: float | None = None,
    count_tokens: int = 0,
    force: bool = False,
    log: Callable[[str], None] = lambda msg: None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[int, dict]:
    """Plan, submit, poll, collect and write; returns ``(exit code, report)``. See the module docstring."""
    wl = load_workload(alignment, k=k, country=country)
    if not _CELEX.match(wl.celex):
        raise BatchError(f"alignment JSON has no valid CELEX number ({wl.celex!r})")
    if model not in PRICES:
        raise BatchError(f"no price for model {model!r}; expected one of {', '.join(PRICES)}")
    judge = AnthropicJudge(client, model, effort=effort, max_tokens=max_tokens, country=wl.country,
                           cache_dir=cache_dir, sleep=sleep)
    if PROMPT_LANGUAGE[wl.country] != wl.language and wl.language:
        log(f"warning: directive text is in {wl.language!r}, the {wl.country} prompt expects "
            f"{PROMPT_LANGUAGE[wl.country]!r}")
    state_file = Path(state_path) if state_path else default_state_path(wl.celex, wl.country, model)
    out_file = Path(out) if out else default_output_path(wl.celex, wl.country)
    state = load_state(state_file, wl, judge)
    if state.get("submitting") and not force:
        raise BatchError(
            f"a submission started at {state['submitting'].get('at')} was interrupted before its batch ID was "
            f"saved in {state_file}; it may be running. Check the Console (Message Batches) and run again with "
            "--force to submit those pairs anyway.")

    scale = 1.0
    if count_tokens:  # free endpoint, but needs a key
        scale = measure_scale(judge, wl.pairs, wl.language, count_tokens)
        log(f"measured input tokens: {scale:.2f} x the character-based estimate")
    out_tokens = output_tokens or _measured_output(state) or DEFAULT_OUTPUT_TOKENS
    report: dict[str, Any] = {
        "celex": wl.celex, "country": wl.country, "language": wl.language, "alignment": wl.source, "k": k,
        "model": model, "prompt_version": judge.prompt_version, "effort": effort, "state": str(state_file),
        "output": str(out_file), "dry_run": dry_run, "max_cost_usd": max_cost,
    }

    # 1. collect batches that have ended
    if not dry_run:
        _collect(wl, judge, state, state_file, report, log)

    plan = make_plan(wl, judge, state, max_attempts)
    by_key = {key: wl.pairs[i] for key, i in plan.first.items()}
    todo_pairs = [by_key[key] for key in plan.todo]
    spent = _spent(state, model)
    running_est = sum(b.get("estimate_upper_usd", 0.0) for b in state["batches"] if not b.get("collected"))
    est = {
        "pairs_to_submit": len(todo_pairs),
        "input_tokens": round(sum(pair_tokens(p, wl.language, scale) for p in todo_pairs)),
        "system_tokens": round(system_tokens(judge, scale)),
        "output_tokens_per_pair": round(out_tokens),
        "token_basis": ("count_tokens sample" if scale != 1.0 else "characters / token"),
        "batch_usd": round(batch_cost(todo_pairs, judge, wl.language, output_tokens=out_tokens, cache_hit=1.0,
                                      scale=scale), 4),
        "batch_upper_usd": round(batch_cost(todo_pairs, judge, wl.language, output_tokens=out_tokens,
                                            cache_hit=0.0, scale=scale), 4),
        "batch_worst_usd": round(worst_cost(todo_pairs, judge, wl.language, scale=scale), 4),
        "max_tokens": judge.max_tokens,
        "standard_usd": round(batch_cost(todo_pairs, judge, wl.language, output_tokens=out_tokens, cache_hit=1.0,
                                         scale=scale, batch=False), 4),
    }
    report.update({
        "pairs": len(wl.pairs), "unique_pairs": len(plan.first), "skipped": wl.skipped,
        "clipped_pairs": sum(1 for p in wl.pairs if p.clipped), "cached": len(plan.cached),
        "pending": len(plan.pending), "to_submit": len(plan.todo), "failed": _count(plan.failed.values()),
        "estimate": est, "spent_usd": round(spent, 4), "running_batches_upper_usd": round(running_est, 4),
    })
    committed = spent + running_est + est["batch_worst_usd"]
    report["projected_upper_usd"] = round(committed, 4)
    if dry_run:
        report["within_max_cost"] = None if max_cost is None else committed <= max_cost
        return (EXIT_BUDGET if report["within_max_cost"] is False else EXIT_OK), report

    # 2. submit what is left (guarded by --max-cost)
    if plan.todo and submit:
        if max_cost is not None and committed > max_cost:
            report["complete"] = False
            report["refused"] = (f"submitting {len(plan.todo)} pairs could bring this run to ${committed:.2f} "
                                 f"(worst case: no cache hits, every answer at {judge.max_tokens} tokens) "
                                 f"> --max-cost ${max_cost:.2f}")
            _write(wl, judge, plan, out_file, report)
            save_state(state_file, state)
            return EXIT_BUDGET, report
        _submit(wl, judge, state, state_file, plan.todo, by_key, scale, out_tokens, log)

    # 3. poll and collect until everything ended or --wait is over
    deadline = clock() + max(0.0, wait)
    while any(not b.get("collected") for b in state["batches"]) and clock() < deadline:
        sleep(min(poll_interval, max(0.0, deadline - clock())))
        _collect(wl, judge, state, state_file, report, log)

    # 4. refusals and invalid output once through the standard API (server-side fallback), if asked
    if retry_interactive:
        _retry_interactive(wl, judge, state, state_file, by_key, max_cost, scale, out_tokens, report, log)

    plan = make_plan(wl, judge, state, max_attempts)
    report.update({"cached": len(plan.cached), "pending": len(plan.pending), "to_submit": len(plan.todo),
                   "failed": _count(plan.failed.values()), "spent_usd": round(_spent(state, model), 4),
                   "batches": [{k2: b.get(k2) for k2 in ("id", "requests", "status", "collected", "submitted")}
                               for b in state["batches"]]})
    _write(wl, judge, plan, out_file, report)
    save_state(state_file, state)
    done = not plan.pending and not plan.todo
    report["complete"] = done
    return (EXIT_OK if done else EXIT_PENDING), report


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def _submit(wl: Workload, judge: AnthropicJudge, state: dict, state_file: Path, todo: list[str],
            by_key: dict[str, Pair], scale: float, out_tokens: float, log: Callable[[str], None]) -> None:
    requests = [{"custom_id": key, "params": judge.request_params(by_key[key].eu, by_key[key].national, ttl="1h")}
                for key in todo]
    for chunk in _chunks(requests):
        ids = [r["custom_id"] for r in chunk]
        upper = worst_cost([by_key[i] for i in ids], judge, wl.language, scale=scale)
        state["submitting"] = {"at": _now(), "requests": len(chunk)}
        save_state(state_file, state)  # a crash after create() leaves this marker, so we never double-submit silently
        try:
            batch = judge.client.messages.batches.create(requests=chunk)
        except Exception as e:  # an SDK error; the SDK is an optional extra, so no isinstance on its classes
            code = getattr(e, "status_code", None)
            if isinstance(code, int) and 400 <= code < 500:
                # The API answered with a client error: no batch was created, nothing can be running.
                state["submitting"] = None
                save_state(state_file, state)
                raise BatchError(f"the API rejected the batch (HTTP {code}): {str(e)[:300]}") from e
            raise BatchError(
                f"submission interrupted ({type(e).__name__}: {str(e)[:200]}); the batch may have been created. "
                "Check the Console (Message Batches) and run again with --force to submit those pairs anyway."
            ) from e
        judge.requests += 1
        state["batches"].append({"id": batch.id, "submitted": _now(), "requests": len(chunk), "custom_ids": ids,
                                 "status": getattr(batch, "processing_status", None), "collected": False,
                                 "estimate_upper_usd": round(upper, 4)})
        for key in ids:
            info = state["pairs"].setdefault(key, {})
            info["attempts"] = int(info.get("attempts", 0)) + 1
            info["batch"] = batch.id
        state["submitting"] = None
        save_state(state_file, state)
        log(f"submitted batch {batch.id}: {len(chunk)} requests (at most ${upper:.2f})")


def _collect(wl: Workload, judge: AnthropicJudge, state: dict, state_file: Path, report: dict,
             log: Callable[[str], None]) -> None:
    pairs: dict[str, Pair] = {}
    for p in wl.pairs:
        pairs.setdefault(judge.cache_key(p.eu, p.national), p)
    batches = None
    for b in state["batches"]:
        if b.get("collected"):
            continue
        batches = batches or judge.client.messages.batches
        info = batches.retrieve(b["id"])
        b["status"] = getattr(info, "processing_status", None)
        if b["status"] != "ended":
            counts = getattr(info, "request_counts", None)
            left = getattr(counts, "processing", None) if counts is not None else None
            log(f"batch {b['id']}: {b['status']}" + (f", {left} requests processing" if left is not None else ""))
            continue
        before = dict(judge.usage_batch)
        seen: dict[str, int] = {}
        unknown = 0
        for res in batches.results(b["id"]):
            key = res.custom_id
            p = pairs.get(key)
            if p is None:  # the alignment JSON changed since submission; the texts to check quotes against are gone
                unknown += 1
                result = getattr(res, "result", None)
                if getattr(result, "type", None) == "succeeded":  # still billed: count it in the budget
                    judge._add_usage(getattr(getattr(result, "message", None), "usage", None), batch=True)
                continue
            _j, outcome = judge.collect_result(res, p.eu, p.national)
            seen[outcome] = seen.get(outcome, 0) + 1
            info2 = state["pairs"].setdefault(key, {})
            info2["outcome"] = outcome
            if outcome == "succeeded":
                state["results"] = int(state.get("results") or 0) + 1
        for k2 in USAGE_KEYS:
            state["usage"][k2] = state["usage"].get(k2, 0) + judge.usage_batch[k2] - before.get(k2, 0)
        b["collected"] = True
        b["outcomes"] = dict(sorted(seen.items()))
        if unknown:
            b["unknown_custom_ids"] = unknown
        save_state(state_file, state)
        log(f"batch {b['id']} collected: {b['outcomes']}" + (f", {unknown} not in this alignment" if unknown else ""))


def _retry_interactive(wl: Workload, judge: AnthropicJudge, state: dict, state_file: Path,
                       by_key: dict[str, Pair], max_cost: float | None, scale: float, out_tokens: float,
                       report: dict, log: Callable[[str], None]) -> None:
    keys = [key for key, info in state["pairs"].items()
            if info.get("outcome") in ("refusal", "invalid_output") and not info.get("interactive")
            and key in by_key]
    done = 0
    for key in keys:
        p = by_key[key]
        one = worst_cost([p], judge, wl.language, scale=scale, batch=False)
        if max_cost is not None and _spent(state, judge.model) + one > max_cost:
            report["interactive_refused"] = f"--max-cost ${max_cost:.2f} reached; {len(keys) - done} pairs left"
            break
        before = dict(judge.usage)
        info = state["pairs"][key]
        info["interactive"] = True
        try:
            judge.judge(p.eu, p.national)
            info["outcome"] = "succeeded"
            state["results"] = int(state.get("results") or 0) + 1
        except Exception as e:  # JudgeError, or an API error after the SDK's retries
            info["interactive_error"] = str(e)[:200]
        for k2 in USAGE_KEYS:
            state["interactive_usage"][k2] = (state["interactive_usage"].get(k2, 0)
                                              + judge.usage[k2] - before.get(k2, 0))
        save_state(state_file, state)
        done += 1
    if keys:
        log(f"interactive retry: {done} of {len(keys)} pairs")


def _write(wl: Workload, judge: AnthropicJudge, plan: Plan, out_file: Path, report: dict) -> None:
    lines = []
    labels: dict[str, int] = {}
    for key, i in plan.first.items():
        p = wl.pairs[i]
        j = judge.cached_judgment(p.eu, p.national)
        if j is None:
            continue
        lines.append(json.dumps(record(wl, p, j), ensure_ascii=False))
        labels[j.label.value] = labels.get(j.label.value, 0) + 1
    report["written"] = len(lines)
    report["labels"] = dict(sorted(labels.items()))
    if not lines:
        return
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_file.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(out_file)


def record(wl: Workload, p: Pair, j: Judgment) -> dict:
    """One JSONL line: the judgment plus where the pair came from."""
    d = j.to_dict()
    d["notes"] = [n for n in d["notes"] if n != "from cache"]
    for side in p.clipped:
        d["notes"].append(f"{side} text was clipped in the alignment JSON; the judge saw only the start")
    return {"celex": wl.celex, "country": wl.country, "candidate_rank": p.rank, "candidate_score": p.score, **d}


def status(alignment: Path | str, *, k: int = 3, model: str = DEFAULT_MODEL, state_path: Path | str | None = None,
           cache_dir: Path | str | None = None, country: str | None = None) -> dict:
    """Offline summary of a state file (no API calls), with the effort and max_tokens the run used."""
    wl = load_workload(alignment, k=k, country=country)
    state_file = Path(state_path) if state_path else default_state_path(wl.celex, wl.country, model)
    settings: dict[str, Any] = {}
    if state_file.exists():
        try:
            saved = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                settings = {"effort": saved.get("effort", "low"), "max_tokens": int(saved.get("max_tokens") or 4096)}
        except (OSError, ValueError, TypeError):
            pass  # load_state below reports a broken file
    judge = AnthropicJudge(None, model, country=wl.country, cache_dir=cache_dir, **settings)
    state = load_state(state_file, wl, judge)
    plan = make_plan(wl, judge, state, max_attempts=10**9)
    return {"state": str(state_file), "exists": state_file.exists(), "unique_pairs": len(plan.first),
            "cached": len(plan.cached), "pending": len(plan.pending), "to_submit": len(plan.todo),
            "failed": _count(plan.failed.values()), "spent_usd": round(_spent(state, model), 4),
            "batches": [{k2: b.get(k2) for k2 in ("id", "requests", "status", "collected", "submitted", "outcomes")}
                        for b in state["batches"]],
            "interrupted_submission": state.get("submitting")}


# --- command line ------------------------------------------------------------------------


def _print_report(r: dict, out: Any = None) -> None:
    e = r.get("estimate", {})
    lines = [
        f"{r['celex']} x {r['country'].upper()} ({r['language']}), {r['model']}, prompt {r['prompt_version']}, k={r['k']}",
        f"pairs: {r['pairs']} ({r['unique_pairs']} unique; {r['clipped_pairs']} with a clipped text; "
        f"skipped {r['skipped'] or 'none'})",
        f"cached: {r['cached']}  pending in batches: {r['pending']}  to submit: {r['to_submit']}  "
        f"final failures: {r['failed'] or 'none'}",
        f"estimate for the pairs to submit: ~{e.get('input_tokens', 0):,} input tokens + {e.get('system_tokens')} "
        f"system tokens per request, {e.get('output_tokens_per_pair')} output tokens per pair ({e.get('token_basis')})",
        f"  batch ${e.get('batch_usd', 0):.2f} (prompt cache hits), ${e.get('batch_upper_usd', 0):.2f} (no hits); "
        f"worst case ${e.get('batch_worst_usd', 0):.2f} (no hits, every answer at {e.get('max_tokens')} tokens); "
        f"standard API ${e.get('standard_usd', 0):.2f}",
        f"spent so far: ${r['spent_usd']:.2f}; projected upper bound (worst case): ${r['projected_upper_usd']:.2f}"
        + (f" (--max-cost ${r['max_cost_usd']:.2f})" if r.get("max_cost_usd") is not None else ""),
    ]
    if "written" in r:
        lines.append(f"wrote {r['written']} judgments to {r['output']}: {r.get('labels')}")
    if r.get("refused"):
        lines.append(f"REFUSED: {r['refused']}")
    if r.get("complete") is False:
        lines.append("not complete: batches still running or pairs left; run the same command again later")
    print("\n".join(lines), file=out or sys.stdout)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m openacquis.judge_batch",
                                 description="Judge all candidate pairs of a directive x country with Message Batches.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="plan, submit, poll, collect and write the JSONL (resumable)")
    s = sub.add_parser("status", help="summary of the state file (offline)")
    for p in (r, s):
        p.add_argument("alignment", help="alignment JSON (site/static-data/<celex>_<cc>.json or align --out)")
        p.add_argument("-k", type=int, default=3, help="candidates per row (default 3)")
        p.add_argument("--country", help="two-letter country code (default: from the JSON)")
        p.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(PRICES))
        p.add_argument("--state", help="state file (default $OPENACQUIS_CACHE/judge-batches/<celex>_<cc>_<model>.json)")
    r.add_argument("--out", help="output JSONL (default data/judgments/<celex>_<cc>.jsonl)")
    r.add_argument("--effort", default="low", help="output_config.effort (default low)")
    r.add_argument("--max-tokens", type=int, default=4096)
    r.add_argument("--dry-run", action="store_true", help="print counts and costs; no key, no API calls")
    r.add_argument("--max-cost", type=float, help="refuse to submit if the projected worst case (no cache hits, "
                                                  "every answer at --max-tokens) exceeds this (USD)")
    r.add_argument("--wait", type=float, default=0.0, help="poll for up to this many seconds (default 0: check once)")
    r.add_argument("--poll", type=float, default=60.0, help="seconds between polls (default 60)")
    r.add_argument("--max-attempts", type=int, default=3, help="submissions per pair for expired / server errors")
    r.add_argument("--no-submit", action="store_true", help="only collect batches already submitted")
    r.add_argument("--retry-interactive", action="store_true",
                   help="send refusals and invalid output once through the standard API (full price, with fallback)")
    r.add_argument("--output-tokens", type=float, help=f"assumed output tokens per pair (default: measured, else "
                                                       f"{DEFAULT_OUTPUT_TOKENS})")
    r.add_argument("--count-tokens", type=int, default=0, metavar="N",
                   help="measure input tokens on N sample pairs with messages.count_tokens (free; needs a key)")
    r.add_argument("--force", action="store_true", help="submit even after an interrupted submission")
    r.add_argument("--report", help="also write the report as JSON to this file")
    args = ap.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    try:
        if args.cmd == "status":
            print(json.dumps(status(args.alignment, k=args.k, model=args.model, state_path=args.state,
                                    country=args.country), ensure_ascii=False, indent=1))
            return EXIT_OK
        code, report = run(
            args.alignment, k=args.k, country=args.country, model=args.model,
            effort=None if args.effort in ("", "none") else args.effort, max_tokens=args.max_tokens,
            state_path=args.state, out=args.out, dry_run=args.dry_run, max_cost=args.max_cost, wait=args.wait,
            poll_interval=args.poll, max_attempts=args.max_attempts, retry_interactive=args.retry_interactive,
            submit=not args.no_submit, output_tokens=args.output_tokens, count_tokens=args.count_tokens,
            force=args.force, log=log)
    except BatchError as e:
        log(f"error: {e}")
        return EXIT_INPUT
    _print_report(report)
    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
