import json
import re
from types import SimpleNamespace

import pytest

from openacquis import judge_batch as jb
from openacquis.judge import FALLBACK_BETA, PROMPT_VERSIONS, Label, system_prompt_for

CELEX = "32022L2555"
EU_ROW = ("eu:32022L2555:cs:art23.par4", "4. bez zbytečného odkladu a v každém případě do 24 hodin poté, co se o "
          "významném incidentu dozvěděl, včasné varování;")
EU_ROW2 = ("eu:32022L2555:cs:art2.par5", "5. Členské státy mohou stanovit, že se tato směrnice vztahuje na subjekty "
           "veřejné správy na místní úrovni.")
NAT = {
    "cz:264/2025:par15.odst1": "(1) Regulovaná služba podá Úřadu nejpozději do 12 hodin poté, co se o významném "
                               "incidentu dozvěděla, včasné varování.",
    "cz:264/2025:par50": "Tento zákon nabývá účinnosti prvním dnem kalendářního měsíce.",
    "cz:264/2025:par2.odst3": "(3) Tento zákon se vztahuje rovněž na subjekty veřejné správy na místní úrovni.",
    "cz:264/2025:par9": "(9) Dlouhý text…",
}


def page_json(tmp_path, country="cz", extra_missing=False):
    """A trimmed website alignment page (site/static-data format): two rows, top candidates per row."""
    rows1 = [{"id": EU_ROW[0], "label": "Čl. 23 odst. 4", "text": EU_ROW[1],
              "machine": [{"id": "cz:264/2025:par15.odst1", "score": 0.61}, {"id": "cz:264/2025:par50", "score": 0.2},
                          {"id": "cz:264/2025:par9", "score": 0.1}, {"id": "cz:264/2025:par2.odst3", "score": 0.05}]}]
    rows2 = [{"id": EU_ROW2[0], "label": "Čl. 2 odst. 5", "text": EU_ROW2[1],
              "machine": [{"id": "cz:264/2025:par2.odst3", "score": 0.7}]
              + ([{"id": "cz:264/2025:missing", "score": 0.1}] if extra_missing else [])}]
    data = {
        "celex": CELEX, "country_code": country, "national_language": "cs",
        "eu": {"language": "cs"},
        "articles": [{"id": "eu:32022L2555:cs:art23", "label": "Článek 23", "heading": "Ohlašovací povinnosti",
                      "rows": rows1},
                     {"id": "eu:32022L2555:cs:art2", "label": "Článek 2", "heading": "Oblast působnosti",
                      "rows": rows2}],
        "national_texts": {k: {"label": k, "text": v} for k, v in NAT.items()},
    }
    p = tmp_path / f"{CELEX}_{country}.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def answer_for(user: str) -> dict:
    """Scripted model answers keyed on the national text in the request."""
    if "do 12 hodin" in user:
        return {"evidence_eu": "do 24 hodin poté", "evidence_national": "nejpozději do 12 hodin poté",
                "rationale": "The directive allows 24 hours, the national act 12 hours.", "label": "stricter",
                "confidence": 0.9}
    if "subjekty veřejné správy" in user and "Členské státy mohou" in user:
        return {"evidence_eu": "Členské státy mohou stanovit",
                "evidence_national": "vztahuje rovněž na subjekty veřejné správy na místní úrovni",
                "rationale": "Option used.", "label": "option", "confidence": 0.8}
    if "subjekty veřejné správy" in user:
        return {"evidence_eu": "", "evidence_national": "", "rationale": "Different subject.",
                "label": "unrelated", "confidence": 0.9}
    return {"evidence_eu": "", "evidence_national": "", "rationale": "Different subject.", "label": "unrelated",
            "confidence": 0.95}


def message(data, stop_reason="end_turn"):
    return SimpleNamespace(
        model="claude-sonnet-5-5", stop_reason=stop_reason, stop_details=None,
        content=[SimpleNamespace(type="text", text=json.dumps(data, ensure_ascii=False))],
        usage=SimpleNamespace(input_tokens=400, output_tokens=300, cache_read_input_tokens=1000,
                              cache_creation_input_tokens=0, cache_creation=None))


class FakeBatches:
    """messages.batches: create / retrieve / results. ``outcome(custom_id, user)`` scripts each result."""

    def __init__(self, outcome=None, ended_after=0):
        self.outcome = outcome or (lambda cid, user: "succeeded")
        self.ended_after = ended_after  # retrieve() calls before a batch reports "ended"
        self.created: list[list[dict]] = []
        self.retrieves: dict[str, int] = {}

    def create(self, requests):
        self.created.append(requests)
        return SimpleNamespace(id=f"msgbatch_{len(self.created)}", processing_status="in_progress")

    def retrieve(self, batch_id):
        n = self.retrieves[batch_id] = self.retrieves.get(batch_id, 0) + 1
        status = "ended" if n > self.ended_after else "in_progress"
        return SimpleNamespace(id=batch_id, processing_status=status,
                               request_counts=SimpleNamespace(processing=0 if status == "ended" else 1))

    def results(self, batch_id):
        reqs = self.created[int(batch_id.rsplit("_", 1)[1]) - 1]
        for req in reversed(reqs):  # any order
            user = req["params"]["messages"][0]["content"]
            kind = self.outcome(req["custom_id"], user)
            if kind == "succeeded":
                result = SimpleNamespace(type="succeeded", message=message(answer_for(user)))
            elif kind == "refusal":
                result = SimpleNamespace(type="succeeded", message=message({}, stop_reason="refusal"))
            elif kind.startswith("errored:"):
                err = SimpleNamespace(type="error", error=SimpleNamespace(type=kind.split(":", 1)[1]))
                result = SimpleNamespace(type="errored", error=err)
            else:
                result = SimpleNamespace(type=kind)
            yield SimpleNamespace(custom_id=req["custom_id"], result=result)


class FakeClient:
    def __init__(self, batches=None, replies=()):
        self.replies = list(replies)
        self.calls = []
        self.messages = SimpleNamespace(batches=batches or FakeBatches(), create=self._create,
                                        count_tokens=self._count)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **params):
        self.calls.append(params)
        return self.replies.pop(0)

    def _count(self, **params):
        return SimpleNamespace(input_tokens=1500)


def go(tmp_path, alignment, client, **kw):
    kw.setdefault("cache_dir", tmp_path / "cache")
    kw.setdefault("state_path", tmp_path / "state.json")
    kw.setdefault("out", tmp_path / "out.jsonl")
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("poll_interval", 0)
    return jb.run(alignment, client=client, **kw)


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_load_page_workload_top_k_and_clipping(tmp_path):
    wl = jb.load_workload(page_json(tmp_path, extra_missing=True), k=3)
    assert (wl.celex, wl.country, wl.language) == (CELEX, "cz", "cs")
    assert [(p.eu.id.rsplit(":", 1)[1], p.national.id, p.rank) for p in wl.pairs] == [
        ("art23.par4", "cz:264/2025:par15.odst1", 1), ("art23.par4", "cz:264/2025:par50", 2),
        ("art23.par4", "cz:264/2025:par9", 3), ("art2.par5", "cz:264/2025:par2.odst3", 1)]
    assert wl.skipped == {"no_national_text": 1}
    assert wl.pairs[0].eu.context == "Článek 23 Ohlašovací povinnosti"
    assert wl.pairs[2].clipped == ["national"] and wl.pairs[0].clipped == []
    with pytest.raises(jb.BatchError, match="not 'sk'"):
        jb.load_workload(page_json(tmp_path), country="sk")


def test_load_align_out_workload(tmp_path):
    data = {"eu": {"celex": CELEX, "language": "cs"},
            "provisions": [{"eu_id": EU_ROW[0], "eu_label": "Čl. 23 odst. 4", "eu_text": EU_ROW[1],
                            "candidates": [{"national_id": "cz:264/2025:par50", "rank": 2, "score": 0.2},
                                           {"national_id": "cz:264/2025:par15.odst1", "rank": 1, "score": 0.6}]}],
            "national_texts": {k: {"id": k, "text": v} for k, v in NAT.items()}}
    p = tmp_path / "align.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    wl = jb.load_workload(p, k=1)
    assert wl.country == "cz" and wl.celex == CELEX
    assert [(x.national.id, x.rank) for x in wl.pairs] == [("cz:264/2025:par15.odst1", 1)]


def test_dry_run_needs_no_client_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    align = page_json(tmp_path)
    code, r = jb.run(align, dry_run=True, cache_dir=tmp_path / "cache", state_path=tmp_path / "s.json",
                     out=tmp_path / "o.jsonl", max_cost=1.0)
    assert code == jb.EXIT_OK and r["within_max_cost"] is True
    assert r["pairs"] == 4 and r["to_submit"] == 4 and r["cached"] == 0
    e = r["estimate"]
    assert 0 < e["batch_usd"] < e["batch_upper_usd"] < 1 and e["standard_usd"] > e["batch_usd"]
    assert not (tmp_path / "s.json").exists() and not (tmp_path / "o.jsonl").exists()
    code, r = jb.run(align, dry_run=True, cache_dir=tmp_path / "cache", state_path=tmp_path / "s.json",
                     max_cost=0.0001)
    assert code == jb.EXIT_BUDGET and r["within_max_cost"] is False


def test_cli_dry_run(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("OPENACQUIS_CACHE", str(tmp_path / "c"))
    rep = tmp_path / "rep.json"
    code = jb.main(["run", str(page_json(tmp_path)), "--dry-run", "--report", str(rep)])
    out = capsys.readouterr().out
    assert code == 0 and "32022L2555 x CZ" in out and "to submit: 4" in out
    assert json.loads(rep.read_text())["unique_pairs"] == 4
    assert jb.main(["run", str(tmp_path / "nope.json"), "--dry-run"]) == jb.EXIT_INPUT


def test_run_submits_polls_collects_and_writes(tmp_path):
    batches = FakeBatches(ended_after=1)
    client = FakeClient(batches)
    code, r = go(tmp_path, page_json(tmp_path), client, wait=10, clock=iter(range(100)).__next__)
    assert code == jb.EXIT_OK and r["complete"]
    assert len(batches.created) == 1 and batches.retrieves["msgbatch_1"] == 2
    req = batches.created[0][0]
    assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", req["custom_id"])
    params = req["params"]
    assert "fallbacks" not in params and "betas" not in params  # not supported in batches
    assert params["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert params["system"][0]["text"] == system_prompt_for("cz")
    assert params["output_config"]["effort"] == "low"

    lines = read_jsonl(tmp_path / "out.jsonl")
    assert len(lines) == 4 and r["written"] == 4
    first = lines[0]
    assert first["celex"] == CELEX and first["country"] == "cz" and first["candidate_rank"] == 1
    assert first["label"] == "stricter" and first["evidence_verified"] and first["status"] == "unreviewed"
    assert first["prompt_version"] == PROMPT_VERSIONS["cz"] and "from cache" not in first["notes"]
    assert any("clipped" in n for n in lines[2]["notes"])
    assert lines[3]["label"] == "option"
    assert r["labels"] == {"option": 1, "stricter": 1, "unrelated": 2}

    state = json.loads((tmp_path / "state.json").read_text())
    assert state["batches"][0]["collected"] and state["results"] == 4
    assert state["usage"]["output_tokens"] == 1200
    assert r["spent_usd"] == pytest.approx(4 * (400 * 2 + 300 * 10 + 1000 * 0.1) / 2e6, abs=1e-4)

    # A second run is free: everything is cached, nothing is submitted.
    code, r2 = go(tmp_path, page_json(tmp_path), FakeClient(FakeBatches()))
    assert code == jb.EXIT_OK and r2["cached"] == 4 and r2["to_submit"] == 0


def test_resume_after_pending_batch(tmp_path):
    align = page_json(tmp_path)
    code, r = go(tmp_path, align, FakeClient(FakeBatches(ended_after=5)), wait=0)
    assert code == jb.EXIT_PENDING and r["pending"] == 4 and not r["complete"]
    assert not (tmp_path / "out.jsonl").exists()
    # Later, a new process with a new client: the batch is collected, not submitted again.
    first_requests = json.loads((tmp_path / "state.json").read_text())["batches"][0]["custom_ids"]
    assert len(first_requests) == 4
    server = FakeBatches()  # the batch from the first run, as the server still holds it
    server.created = [[{"custom_id": cid, "params": req} for cid, req in _requests(tmp_path, align)]]
    code, r = go(tmp_path, align, FakeClient(server), wait=0)
    assert code == jb.EXIT_OK and len(server.created) == 1 and r["written"] == 4


def _requests(tmp_path, align):
    from openacquis.judge import AnthropicJudge

    wl = jb.load_workload(align)
    j = AnthropicJudge(None, country="cz", cache_dir=tmp_path / "cache")
    return [(j.cache_key(p.eu, p.national), j.request_params(p.eu, p.national, ttl="1h")) for p in wl.pairs]


def test_failures_are_retried_or_final(tmp_path):
    align = page_json(tmp_path)
    keys = {p.national.id: k for k, p in zip([k for k, _ in _requests(tmp_path, align)], jb.load_workload(align).pairs)}
    script = {keys["cz:264/2025:par50"]: "expired", keys["cz:264/2025:par9"]: "errored:invalid_request",
              keys["cz:264/2025:par2.odst3"]: "refusal"}
    batches = FakeBatches(outcome=lambda cid, user: script.get(cid, "succeeded"))
    code, r = go(tmp_path, align, FakeClient(batches), wait=10, clock=iter(range(100)).__next__)
    # the expired pair is re-submitted on the next run; invalid_request and refusal are final
    assert code == jb.EXIT_PENDING and r["to_submit"] == 1
    assert r["failed"] == {"errored:invalid_request": 1, "refusal": 1}
    assert len(read_jsonl(tmp_path / "out.jsonl")) == 1

    script[keys["cz:264/2025:par50"]] = "succeeded"
    code, r = go(tmp_path, align, FakeClient(batches), wait=10, clock=iter(range(100)).__next__)
    assert code == jb.EXIT_OK and len(batches.created) == 2 and len(batches.created[1]) == 1
    assert r["written"] == 2

    # Expired every time: given up after --max-attempts submissions.
    tmp2 = tmp_path / "again"
    tmp2.mkdir()
    always = FakeBatches(outcome=lambda cid, user: "expired")
    for _ in range(2):
        code, r = go(tmp2, align, FakeClient(always), wait=10, clock=iter(range(100)).__next__, max_attempts=2)
    assert code == jb.EXIT_OK and r["failed"] == {"expired (after 2 attempts)": 4} and len(always.created) == 2


def test_retry_interactive_uses_standard_api_with_fallback(tmp_path):
    align = page_json(tmp_path)
    batches = FakeBatches(outcome=lambda cid, user: "refusal" if "do 12 hodin" in user else "succeeded")
    reply = message(answer_for("do 12 hodin"))
    client = FakeClient(batches, replies=[reply])
    code, r = go(tmp_path, align, client, wait=10, clock=iter(range(100)).__next__, retry_interactive=True)
    assert code == jb.EXIT_OK and r["written"] == 4 and r["failed"] == {}
    assert client.calls[0]["betas"] == [FALLBACK_BETA] and client.calls[0]["fallbacks"] == "default"
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["interactive_usage"]["output_tokens"] == 300


def test_max_cost_refuses_before_submitting(tmp_path):
    batches = FakeBatches()
    code, r = go(tmp_path, page_json(tmp_path), FakeClient(batches), max_cost=0.0001)
    assert code == jb.EXIT_BUDGET and batches.created == [] and "max-cost" in r["refused"]


def test_interrupted_submission_is_not_resubmitted_silently(tmp_path):
    class Crash(FakeBatches):
        def create(self, requests):
            raise ConnectionError("network down after the server accepted the batch?")

    align = page_json(tmp_path)
    with pytest.raises(jb.BatchError, match="may have been created"):
        go(tmp_path, align, FakeClient(Crash()))
    with pytest.raises(jb.BatchError, match="interrupted"):
        go(tmp_path, align, FakeClient(FakeBatches()))
    code, _ = go(tmp_path, align, FakeClient(FakeBatches()), force=True, wait=10, clock=iter(range(100)).__next__)
    assert code == jb.EXIT_OK


def test_state_must_match_the_run(tmp_path):
    align = page_json(tmp_path)
    go(tmp_path, align, FakeClient(FakeBatches(ended_after=9)))
    with pytest.raises(jb.BatchError, match="model"):
        go(tmp_path, align, FakeClient(), model="claude-opus-5-5")
    with pytest.raises(jb.BatchError, match="running batches"):
        go(tmp_path, align, FakeClient(), effort="medium")


def test_chunks_respect_request_limit(monkeypatch):
    monkeypatch.setattr(jb, "MAX_REQUESTS_PER_BATCH", 2)
    reqs = [{"custom_id": str(i), "params": {}} for i in range(5)]
    assert [len(c) for c in jb._chunks(reqs)] == [2, 2, 1]
    monkeypatch.setattr(jb, "MAX_BYTES_PER_BATCH", 60)
    assert [len(c) for c in jb._chunks(reqs)] == [1, 1, 1, 1, 1]


def test_country_prompt_used_for_slovak(tmp_path):
    batches = FakeBatches()
    go(tmp_path, page_json(tmp_path, country="sk"), FakeClient(batches), wait=10, clock=iter(range(100)).__next__)
    assert batches.created[0][0]["params"]["system"][0]["text"] == system_prompt_for("sk")
    line = read_jsonl(tmp_path / "out.jsonl")[0]
    assert line["prompt_version"] == PROMPT_VERSIONS["sk"] and line["country"] == "sk"
    assert Label(line["label"])


def test_count_tokens_scales_the_estimate(tmp_path):
    code, r = go(tmp_path, page_json(tmp_path), FakeClient(), dry_run=True, count_tokens=2)
    assert r["estimate"]["token_basis"] == "count_tokens sample"


def test_status_is_offline(tmp_path):
    align = page_json(tmp_path)
    go(tmp_path, align, FakeClient(FakeBatches(ended_after=9)))
    s = jb.status(align, state_path=tmp_path / "state.json", cache_dir=tmp_path / "cache")
    assert s["pending"] == 4 and s["batches"][0]["id"] == "msgbatch_1" and s["spent_usd"] == 0


def test_rejected_submission_clears_the_marker(tmp_path):
    class Rejected(Exception):
        status_code = 413

    class Reject(FakeBatches):
        def create(self, requests):
            raise Rejected("request too large")

    align = page_json(tmp_path)
    with pytest.raises(jb.BatchError, match="HTTP 413"):
        go(tmp_path, align, FakeClient(Reject()))
    assert json.loads((tmp_path / "state.json").read_text())["submitting"] is None
    code, _ = go(tmp_path, align, FakeClient(FakeBatches()), wait=10, clock=iter(range(100)).__next__)
    assert code == jb.EXIT_OK


def test_unknown_custom_ids_still_count_their_usage(tmp_path):
    align = page_json(tmp_path)
    go(tmp_path, align, FakeClient(FakeBatches(ended_after=9)))
    batches = FakeBatches()
    # the alignment JSON changed since submission: none of the results matches a pair any more
    batches.results = lambda bid: iter([SimpleNamespace(custom_id=f"gone{i}", result=SimpleNamespace(
        type="succeeded", message=message({}))) for i in range(4)])
    client = FakeClient(batches)
    code, r = go(tmp_path, align, client, max_cost=100, submit=False)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["batches"][0]["unknown_custom_ids"] == 4
    assert state["usage"]["output_tokens"] == 4 * 300 and r["spent_usd"] > 0


def test_max_cost_guards_the_worst_case(tmp_path):
    align = page_json(tmp_path)
    _, r = go(tmp_path, align, FakeClient(), dry_run=True)
    e = r["estimate"]
    assert e["batch_upper_usd"] < e["batch_worst_usd"] == r["projected_upper_usd"]
    cap = (e["batch_upper_usd"] + e["batch_worst_usd"]) / 2
    batches = FakeBatches()
    code, r = go(tmp_path, align, FakeClient(batches), max_cost=cap)
    assert code == jb.EXIT_BUDGET and batches.created == [] and "worst case" in r["refused"]


def test_status_uses_the_runs_settings(tmp_path):
    align = page_json(tmp_path)
    go(tmp_path, align, FakeClient(FakeBatches(ended_after=9)), effort="medium")
    s = jb.status(align, state_path=tmp_path / "state.json", cache_dir=tmp_path / "cache")
    assert s["pending"] == 4
    go(tmp_path / "b", align, FakeClient(), max_tokens=8000, wait=10, clock=iter(range(100)).__next__,
       state_path=tmp_path / "s2.json", cache_dir=tmp_path / "c2")
    s = jb.status(align, state_path=tmp_path / "s2.json", cache_dir=tmp_path / "c2")
    assert s["cached"] == 4 and s["to_submit"] == 0
