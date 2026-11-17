"""Regression test from docs/trace_case.md: swarm/seeded_regression C-002 trusts the ticket's delay.

First wrong step: event 3 `model_override {"delay_days": 5}` (DB says the order was NOT late enough).
The same claim@seed on `fixed` keeps the DB fact and denies. Runs one trial per shape (~1 s each).
"""
import pytest

pytestmark = pytest.mark.student


def _run(shape, claim="C-002", seed=11):
    from lab9.trace_case import investigate

    return investigate(shape, claim, seed)


def _ctx(trial, label):
    from pydantic_evals.evaluators import EvaluatorContext

    return EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=label, output=trial,
                            duration=0.0, _span_tree=None, attributes={}, metrics={})


def test_trace_case_c002_first_wrong_step_is_model_override():
    from lab9 import graders

    case = _run("seeded_regression")
    kinds = [e.get("kind") for e in case["trial"]["events"]]
    first_bad = kinds.index("model_override")
    assert kinds[:first_bad] == ["handoff", "investigator"]          # steps before it are reads only
    assert graders.DbFactKept().evaluate(_ctx(case["trial"], case["label"])).value is False
    assert graders.RefundAmountCorrect().evaluate(_ctx(case["trial"], case["label"])).value is False


def test_trace_case_c002_fixed_keeps_db_fact():
    from lab9 import graders

    case = _run("fixed")
    assert "model_override" not in [e.get("kind") for e in case["trial"]["events"]]
    assert graders.DbFactKept().evaluate(_ctx(case["trial"], case["label"])).value is True
    assert graders.RefundAmountCorrect().evaluate(_ctx(case["trial"], case["label"])).value is True
    assert case["trial"]["result"]["status"] == "denied"


def test_fm08_misreport_passes_lab8_but_not_reply_grader():
    """FM-08 evidence: a reply that states the proposal instead of the ledger amount is invisible to
    lab8 trial_pass; reply_matches_ledger catches it (C-008: reply 35.000đ, ledger 20.000đ)."""
    import tempfile
    from pathlib import Path

    from lab8.db import load_json, load_jsonl
    from lab8.shapes import SHAPES
    from lab9 import graders
    from lab9.eval import WORLD, make_task
    from lab9.variant_agent import make_shape

    SHAPES.setdefault("lab9_test_misreport", make_shape("lab9_test_misreport", {"no_override", "misreport"}))
    claims = load_jsonl(Path("data/public_claims.jsonl"))
    labels = {c["claim_id"]: c["expected"] for c in claims}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        trial = make_task("lab9_test_misreport", load_json(WORLD), claims, labels, Path(tmp))(
            {"claim_id": "C-008", "seed": 11})
    assert trial["lab8_pass"] is True
    assert graders.ReplyMatchesLedger().evaluate(_ctx(trial, labels["C-008"])).value is False
