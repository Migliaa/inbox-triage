"""`route` turns probabilities into an outcome. No model is loaded here."""

import pytest

from src.decider import INJECTION, UNCERTAIN, route


def probs(**given):
    base = {"billing": 0.0, "bug_report": 0.0, "sales_lead": 0.0, "spam": 0.0}
    return {**base, **given}


def test_confident_label_is_accepted():
    assert route(probs(billing=0.96, spam=0.04), 0.1).outcome == "billing"


def test_below_threshold_is_uncertain():
    decision = route(probs(billing=0.80, sales_lead=0.17), 0.05)
    assert decision.outcome == UNCERTAIN and decision.top_label == "billing"


def test_injection_overrides_a_confident_acting_label():
    assert route(probs(billing=0.97), 0.9).outcome == INJECTION


def test_confident_spam_stays_spam_even_if_it_looks_like_an_injection():
    assert route(probs(spam=0.94), 0.76).outcome == "spam"


def test_unsure_spam_with_injection_is_an_injection():
    assert route(probs(spam=0.36, sales_lead=0.38), 0.94).outcome == INJECTION


def test_threshold_can_be_moved():
    assert route(probs(billing=0.80, sales_lead=0.17), 0.05, threshold=0.75).outcome == "billing"


def test_recorded_answers_are_replayed_and_an_unknown_text_is_refused(monkeypatch, tmp_path):
    import json

    from src import decider

    known = {"from": "a@b.c", "subject": "Invoice", "body": "charged twice"}
    answer = {"probabilities": {"billing": 0.97, "bug_report": 0.01, "sales_lead": 0.01, "spam": 0.01}, "injection": 0.02}
    file = tmp_path / "answers.json"
    file.write_text(json.dumps({"answers": {decider.email_key(known): answer}}), encoding="utf-8")
    monkeypatch.setattr(decider, "RECORDED_ANSWERS", str(file))
    decider._recorded.cache_clear()

    probabilities, injection = decider.ask_model(known)
    assert probabilities["billing"] == 0.97 and injection == 0.02
    with pytest.raises(decider.NotRecorded):
        decider.ask_model({**known, "body": "charged three times"})
    decider._recorded.cache_clear()
