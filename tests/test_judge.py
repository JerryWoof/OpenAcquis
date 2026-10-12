import json
import os
from types import SimpleNamespace

import pytest

from openacquis.judge import (
    DEFAULT_MODEL,
    FALLBACK_BETA,
    MISSING,
    OUTPUT_SCHEMA,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    AnthropicJudge,
    FakeJudge,
    Judgment,
    JudgeError,
    Label,
    Passage,
    RuleJudge,
    as_passage,
    build_user_message,
    estimate_cost,
    locate_quote,
    provision_status,
    quantities,
)
from openacquis.provisions import Document, Provision, make_id

# Small Czech texts modelled on NIS2 Art. 23(4)(a) and a Czech implementing provision.
EU = Passage(
    "eu:32022L2555:cs:art23.par4.pt_a",
    "bez zbytečného odkladu a v každém případě do 24 hodin poté, co se o významném incidentu dozvěděl, "
    "včasné varování, v němž se případně uvede, zda je podezření, že významný incident byl způsoben "
    "protiprávním nebo zlovolným jednáním nebo by mohl mít přeshraniční dopad;",
)
NAT_SAME = Passage(
    "cz:264/2025:par15.odst1",
    "Regulovaná služba podá Úřadu bez zbytečného odkladu, nejpozději však do 24 hodin poté, co se o významném "
    "incidentu dozvěděla, včasné varování, v němž uvede, zda je podezření, že významný incident byl způsoben "
    "protiprávním nebo zlovolným jednáním nebo by mohl mít přeshraniční dopad.",
)
NAT_SHORTER = Passage(
    "cz:999/2025:par3",
    "Poskytovatel podá bez zbytečného odkladu, nejpozději do 12 hodin poté, co se o významném incidentu "
    "dozvěděl, včasné varování o významném incidentu.",
)
NAT_LONGER = Passage(
    "cz:999/2025:par4",
    "Poskytovatel podá bez zbytečného odkladu, nejpozději do 48 hodin poté, co se o významném incidentu "
    "dozvěděl, včasné varování o významném incidentu.",
)
NAT_UNRELATED = Passage(
    "cz:264/2025:par50",
    "Tento zákon nabývá účinnosti prvním dnem kalendářního měsíce následujícího po dni jeho vyhlášení.",
)
EU_OPTION = Passage(
    "eu:32022L2555:cs:art2.par5",
    "Členské státy mohou stanovit, že se tato směrnice vztahuje na subjekty veřejné správy na místní úrovni.",
)
NAT_OPTION = Passage(
    "cz:264/2025:par2.odst3",
    "Tento zákon se vztahuje rovněž na subjekty veřejné správy na místní úrovni, zejména obce s rozšířenou působností.",
)


# --- quotes ----------------------------------------------------------------


def test_locate_quote_tolerates_whitespace_case_and_typographic_quotes():
    src = "Lhůta činí „24 hodin“ od zjištění."
    span, elided = locate_quote('lhůta činí "24  hodin"', src)
    assert span == (0, 21) and not elided
    assert src[span[0]:span[1]] == "Lhůta činí „24 hodin“"


def test_locate_quote_respects_word_boundaries():
    src = "Hlášení podá nejpozději do 240 minut, a do 12 hodin závěrečnou zprávu."
    assert locate_quote("24", src) == (None, False)
    assert locate_quote("do 24", src) == (None, False)
    span, _ = locate_quote("do 240 minut", src)
    assert src[span[0]:span[1]] == "do 240 minut"
    span, _ = locate_quote("(240", "lhůta (240 minut)")
    assert span == (6, 10)


def test_short_and_elided_quotes_are_not_verified():
    from openacquis.judge import _judgment_from_output

    eu = Passage("eu:x:cs:art1", "Členské státy zajistí oznámení do 24 hodin od zjištění.")
    nat = Passage("cz:1/2025:par1", "Povinná osoba oznámí incident nejpozději do 240 minut, a do 12 hodin zprávu.")
    j = _judgment_from_output({"label": "stricter", "confidence": 0.95, "evidence_eu": "24",
                               "evidence_national": "24"}, eu, nat, "m", "v")
    assert not j.evidence_verified and j.evidence_national_span is None and j.confidence < 0.5
    j = _judgment_from_output({"label": "same", "confidence": 0.9, "evidence_eu": "a",
                               "evidence_national": "a"}, eu, nat, "m", "v")
    assert not j.evidence_verified and j.confidence < 0.9
    assert any("too short" in n for n in j.notes)
    j = _judgment_from_output({"label": "same", "confidence": 0.9, "evidence_eu": "Členské státy ... 24 hodin",
                               "evidence_national": "Povinná osoba ... do 12 hodin"}, eu, nat, "m", "v")
    assert not j.evidence_verified and any("elided" in n for n in j.notes)


@pytest.mark.parametrize("raw, expected", [(float("nan"), 0.0), (float("inf"), 0.0), (85, 0.85), (-1, 0.0),
                                           (1000, 0.0), ("high", 0.0), (0.7, 0.7)])
def test_confidence_is_never_promoted_to_full(raw, expected):
    from openacquis.judge import _judgment_from_output

    out = {"label": "unrelated", "confidence": raw, "evidence_eu": "", "evidence_national": ""}
    j = _judgment_from_output(out, EU, NAT_UNRELATED, "m", "v")
    assert j.confidence == pytest.approx(expected)
    assert (raw == 0.7) == (not any("confidence" in n for n in j.notes))


def test_locate_quote_elided_and_missing():
    span, elided = locate_quote("bez zbytečného odkladu … včasné varování", EU.text)
    assert span is not None and elided
    assert locate_quote("do 72 hodin", EU.text) == (None, False)
    assert locate_quote("   ", EU.text) == (None, False)


def test_quantities_units_and_number_words():
    q = quantities("do 24 hodin, do jednoho měsíce, nejméně 10 000 000 EUR nebo 2 % obratu; článek 23 odst. 4 směrnice 2022/2555")
    assert [(v, u) for v, u, _ in q] == [(24, "hour"), (1, "month"), (10_000_000, "eur"), (2, "percent")]


# --- rule judge ------------------------------------------------------------


@pytest.mark.parametrize(
    "nat, label",
    [
        (NAT_SAME, Label.SAME),
        (NAT_SHORTER, Label.STRICTER),
        (NAT_LONGER, Label.DIFFERENT_VALUE),
        (NAT_UNRELATED, Label.UNRELATED),
    ],
)
def test_rule_judge_labels(nat, label):
    j = RuleJudge().judge(EU, nat)
    assert j.label == label
    assert j.method == "heuristic" and j.model == "rule-judge" and j.status == "unreviewed"
    assert 0 <= j.confidence <= 0.4
    assert j.rationale.startswith("Heuristic:")
    if label != Label.UNRELATED:
        assert j.evidence_verified
        assert EU.text[slice(*j.evidence_eu_span)].startswith(j.evidence_eu[:10])
        assert j.evidence_national in nat.text


def test_rule_judge_deadline_evidence_contains_the_values():
    j = RuleJudge().judge(EU, NAT_LONGER)
    assert "24 hodin" in j.evidence_eu and "48 hodin" in j.evidence_national
    assert "24" in j.rationale and "48" in j.rationale


def test_rule_judge_option():
    j = RuleJudge().judge(EU_OPTION, NAT_OPTION)
    assert j.label == Label.OPTION
    assert "mohou stanovit" in j.evidence_eu


def test_rule_judge_accepts_provisions_and_tuples():
    p = Provision(id=EU.id, source="eu", act_id="32022L2555", language="cs", kind="point", label="a)",
                  number="a", text=EU.text)
    j = RuleJudge().judge(p, (NAT_SAME.id, NAT_SAME.text))
    assert j.eu_id == EU.id and j.national_id == NAT_SAME.id
    with pytest.raises(TypeError):
        as_passage(42)


def test_passage_from_provision_with_document_context():
    doc = Document("eu", "32022L2555", "cs")
    art = doc.add(Provision(make_id("eu", "32022L2555", [("article", 23)], "cs"), "eu", "32022L2555", "cs",
                            "article", "Článek 23", "23", heading="Ohlašovací povinnosti"))
    par = doc.add(Provision(art.id + ".par4", "eu", "32022L2555", "cs", "paragraph", "4.", "4",
                            text="Pro účely oznámení předloží dotčené subjekty:", parent_id=art.id))
    doc.add(Provision(par.id + ".pt_a", "eu", "32022L2555", "cs", "point", "a)", "a", text=EU.text, parent_id=par.id))
    p = Passage.from_provision(par, doc)
    assert "a) bez zbytečného odkladu" in p.text
    assert p.context == "Článek 23 Ohlašovací povinnosti"


# --- provision-level status ------------------------------------------------


def test_provision_status_missing_and_best():
    fj = FakeJudge({(EU.id, NAT_SAME.id): Label.SAME})
    js = fj.judge_many([(EU, NAT_UNRELATED), (EU, NAT_SAME)])
    status, best = provision_status(js)
    assert status == "same" and best.national_id == NAT_SAME.id
    assert provision_status(js[:1]) == (MISSING, None)
    assert provision_status([]) == (MISSING, None)


def test_judgment_roundtrip():
    j = RuleJudge().judge(EU, NAT_SHORTER)
    d = json.loads(json.dumps(j.to_dict(), ensure_ascii=False))
    assert d["label"] == "stricter"
    assert Judgment.from_dict(d) == j


# --- Anthropic judge with a fake SDK client ----------------------------------


def message(data, stop_reason="end_turn", model=DEFAULT_MODEL, raw=None):
    text = raw if raw is not None else json.dumps(data, ensure_ascii=False)
    return SimpleNamespace(
        id="msg_1",
        model=model,
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=500, output_tokens=300, cache_read_input_tokens=1000,
                              cache_creation_input_tokens=0),
    )


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **params):
        self.calls.append(params)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeBatches:
    def __init__(self, answer):
        self.answer = answer  # custom_id -> message or None (errored)
        self.created = []
        self.polls = 0

    def create(self, requests):
        self.created.append(requests)
        return SimpleNamespace(id="batch_1", processing_status="in_progress")

    def retrieve(self, batch_id):
        self.polls += 1
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def results(self, batch_id):
        for req in self.created[-1]:
            msg = self.answer(req)
            if msg is None:
                yield SimpleNamespace(custom_id=req["custom_id"], result=SimpleNamespace(type="errored"))
            else:
                yield SimpleNamespace(custom_id=req["custom_id"],
                                      result=SimpleNamespace(type="succeeded", message=msg))


class FakeClient:
    """Mimics anthropic.Anthropic: messages.create, beta.messages.create, messages.batches."""

    def __init__(self, replies=(), batch_answer=None):
        self.messages = FakeMessages(replies)
        self.messages.batches = FakeBatches(batch_answer or (lambda req: None))
        self.beta = SimpleNamespace(messages=self.messages)


GOOD = {
    "evidence_eu": "do 24 hodin poté, co se o významném incidentu dozvěděl",
    "evidence_national": "nejpozději do 12 hodin poté",
    "rationale": "Both require an early warning; the directive allows 24 hours, the Czech text 12 hours.",
    "label": "stricter",
    "confidence": 0.9,
}


def make_judge(tmp_path, replies=(), **kw):
    client = FakeClient(replies, kw.pop("batch_answer", None))
    return AnthropicJudge(client, cache_dir=tmp_path, sleep=lambda s: None, **kw), client


def test_anthropic_request_shape(tmp_path):
    judge, client = make_judge(tmp_path, [message(GOOD)])
    j = judge.judge(EU, NAT_SHORTER)
    params = client.messages.calls[0]
    assert params["model"] == "claude-sonnet-5-5"
    assert params["system"][0]["text"] == SYSTEM_PROMPT
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["output_config"]["format"] == {"type": "json_schema", "schema": OUTPUT_SCHEMA}
    assert params["output_config"]["effort"] == "low"
    assert "temperature" not in params and "tool_choice" not in params
    assert params["betas"] == [FALLBACK_BETA] and params["fallbacks"] == "default"
    user = params["messages"][0]["content"]
    assert EU.id in user and NAT_SHORTER.text in user and SYSTEM_PROMPT not in user

    assert j.label == Label.STRICTER and j.confidence == 0.9 and j.evidence_verified
    assert j.model == DEFAULT_MODEL and j.prompt_version == PROMPT_VERSION and j.method == "llm"
    assert EU.text[slice(*j.evidence_eu_span)] == GOOD["evidence_eu"]
    assert judge.usage["cache_read_input_tokens"] == 1000 and judge.requests == 1
    assert judge.cost() == pytest.approx((500 * 2 + 300 * 10 + 1000 * 0.2) / 1e6)


def test_anthropic_no_fallbacks_uses_plain_endpoint_and_temperature_opt_in(tmp_path):
    judge, client = make_judge(tmp_path, [message(GOOD)], fallbacks=False, temperature=0.0, effort=None)
    judge.judge(EU, NAT_SHORTER)
    params = client.messages.calls[0]
    assert "betas" not in params and params["temperature"] == 0.0 and "effort" not in params["output_config"]


def test_anthropic_cache_hit_makes_no_request(tmp_path):
    judge, client = make_judge(tmp_path, [message(GOOD)])
    first = judge.judge(EU, NAT_SHORTER)
    again, client2 = make_judge(tmp_path, [])  # new judge, same cache dir, no replies queued
    second = again.judge(EU, NAT_SHORTER)
    assert again.requests == 0 and again.cache_hits == 1
    assert second.label == first.label and "from cache" in second.notes
    # A different prompt version or text is a different key.
    other, _ = make_judge(tmp_path, [message(GOOD)], prompt_version="test-2")
    other.judge(EU, NAT_SHORTER)
    assert other.requests == 1


def test_anthropic_downgrades_unverified_quotes(tmp_path):
    bad = dict(GOOD, evidence_national="nejpozději do šesti hodin")  # not in the text
    judge, _ = make_judge(tmp_path, [message(bad)])
    j = judge.judge(EU, NAT_SHORTER)
    assert not j.evidence_verified and j.evidence_national_span is None
    assert j.confidence == pytest.approx(0.45)
    assert any("not found" in n for n in j.notes)


def test_anthropic_related_label_without_quote_is_downgraded(tmp_path):
    judge, _ = make_judge(tmp_path, [message(dict(GOOD, evidence_eu=""))])
    j = judge.judge(EU, NAT_SHORTER)
    assert not j.evidence_verified and j.confidence == pytest.approx(0.45)


def test_anthropic_unrelated_with_empty_quotes_is_fine(tmp_path):
    out = {"evidence_eu": "", "evidence_national": "", "rationale": "Different subject.",
           "label": "unrelated", "confidence": 0.95}
    judge, _ = make_judge(tmp_path, [message(out)])
    j = judge.judge(EU, NAT_UNRELATED)
    assert j.label == Label.UNRELATED and j.evidence_verified and j.confidence == 0.95


def test_anthropic_retries_malformed_output_then_succeeds(tmp_path):
    judge, client = make_judge(tmp_path, [message(None, raw="not json"), message(GOOD)])
    j = judge.judge(EU, NAT_SHORTER)
    assert j.label == Label.STRICTER and len(client.messages.calls) == 2


def test_anthropic_errors(tmp_path):
    judge, client = make_judge(tmp_path, [message(GOOD, stop_reason="refusal")])
    with pytest.raises(JudgeError, match="refusal"):
        judge.judge(EU, NAT_SHORTER)
    assert len(client.messages.calls) == 1  # refusals are not retried
    sleeps = []
    client = FakeClient([message(GOOD, stop_reason="max_tokens")] * 3)
    judge = AnthropicJudge(client, cache_dir=tmp_path, sleep=sleeps.append, attempts=3)
    with pytest.raises(JudgeError, match="truncated"):
        judge.judge(EU, NAT_SHORTER)
    assert len(client.messages.calls) == 1 and sleeps == []  # identical paid retries would truncate again
    judge, _ = make_judge(tmp_path, [message(dict(GOOD, label="breach"))] * 2)
    with pytest.raises(JudgeError, match="invalid label"):
        judge.judge(EU, NAT_SHORTER)


def test_anthropic_no_sleep_after_last_attempt(tmp_path):
    sleeps = []
    client = FakeClient([message(None, raw="not json")] * 2)
    judge = AnthropicJudge(client, cache_dir=tmp_path, sleep=sleeps.append, attempts=2)
    with pytest.raises(JudgeError, match="not JSON"):
        judge.judge(EU, NAT_SHORTER)
    assert len(client.messages.calls) == 2 and sleeps == [1.0]


def test_anthropic_cache_key_includes_request_settings(tmp_path):
    base = AnthropicJudge(FakeClient(), cache_dir=tmp_path)
    for kw in ({"temperature": 1.0}, {"max_tokens": 100}, {"fallbacks": False}):
        assert AnthropicJudge(FakeClient(), cache_dir=tmp_path, **kw).cache_key(EU, NAT_SAME) != base.cache_key(EU, NAT_SAME)


def test_anthropic_records_served_model(tmp_path):
    judge, _ = make_judge(tmp_path, [message(GOOD, model="claude-opus-5-5")])
    assert judge.judge(EU, NAT_SHORTER).model == "claude-opus-5-5"


def test_anthropic_batch(tmp_path):
    def answer(req):
        user = req["params"]["messages"][0]["content"]
        if NAT_UNRELATED.id in user:
            return None  # errored request
        return message(GOOD)

    judge, client = make_judge(tmp_path, [message(GOOD)], batch_answer=answer)
    judge.judge(EU, NAT_SHORTER)  # cached beforehand
    pairs = [(EU, NAT_SHORTER), (EU, NAT_LONGER), (EU, NAT_UNRELATED), (EU, NAT_LONGER)]
    out = judge.judge_batch(pairs, poll_interval=0)
    sent = client.messages.batches.created[0]
    assert len(sent) == 2  # cached pair skipped, duplicate pair sent once
    assert all("fallbacks" not in r["params"] for r in sent)
    assert out[0].label == Label.STRICTER and "from cache" in out[0].notes
    assert out[1].label == Label.STRICTER and out[3].label == Label.STRICTER
    assert not out[1].evidence_verified  # "12 hodin" is not in NAT_LONGER
    assert out[2] is None


def test_user_message_escapes_and_keeps_context_separate():
    msg = build_user_message(Passage("eu:x", "a < b & c", context="Článek 1"), Passage("cz:y", "text"))
    assert "a &lt; b &amp; c" in msg and "<context>Článek 1</context>" in msg


def test_cost_estimate():
    assert estimate_cost(0) == 0
    one = estimate_cost(2500)
    assert estimate_cost(2500, batch=True) == pytest.approx(one / 2)
    assert estimate_cost(2500, "claude-haiku-5-5") < one < estimate_cost(2500, "claude-opus-5-5")
    assert 5 < one < 30


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_live_anthropic_judge(tmp_path):
    pytest.importorskip("anthropic")
    j = AnthropicJudge(cache_dir=tmp_path).judge(EU, NAT_SHORTER)
    assert j.label in (Label.STRICTER, Label.DIFFERENT_VALUE)
    assert j.evidence_verified
