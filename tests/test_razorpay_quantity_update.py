"""update_subscription_quantity(): card-mandate subscriptions can't change
mid-cycle, so the change is scheduled for the cycle end instead."""
from utils import razorpay_utils

CARD_MANDATE_ERROR = ("Razorpay request failed: Can't update subscription immediately "
                      "when card mandate is applicable")


def _fake_razorpay(monkeypatch, responses):
    """Each call pops the next (data, error) from responses; returns the call log."""
    calls = []

    def fake_request(path, body=None, method="POST"):
        calls.append((path, body, method))
        return responses.pop(0)

    monkeypatch.setattr(razorpay_utils, "_request", fake_request)
    return calls


def test_updates_immediately_when_razorpay_allows(monkeypatch):
    calls = _fake_razorpay(monkeypatch, [({"id": "sub_1"}, None)])
    assert razorpay_utils.update_subscription_quantity("sub_1", 4) == (True, None)
    assert calls == [("/subscriptions/sub_1", {"quantity": 4, "schedule_change_at": "now"}, "PATCH")]


def test_card_mandate_schedules_change_for_cycle_end(monkeypatch):
    calls = _fake_razorpay(monkeypatch, [(None, CARD_MANDATE_ERROR), ({"id": "sub_1"}, None)])
    assert razorpay_utils.update_subscription_quantity("sub_1", 4) == (True, None)
    assert calls[1] == ("/subscriptions/sub_1", {"quantity": 4, "schedule_change_at": "cycle_end"}, "PATCH")
    assert len(calls) == 2


def test_card_mandate_replaces_an_earlier_scheduled_change(monkeypatch):
    calls = _fake_razorpay(monkeypatch, [
        (None, CARD_MANDATE_ERROR),
        (None, "Razorpay request failed: subscription already has a scheduled change"),
        ({}, None),  # cancel_scheduled_changes
        ({"id": "sub_1"}, None),
    ])
    assert razorpay_utils.update_subscription_quantity("sub_1", 5) == (True, None)
    assert calls[2] == ("/subscriptions/sub_1/cancel_scheduled_changes", None, "POST")
    assert calls[3] == ("/subscriptions/sub_1", {"quantity": 5, "schedule_change_at": "cycle_end"}, "PATCH")


def test_card_mandate_reports_failure_when_scheduling_fails(monkeypatch):
    _fake_razorpay(monkeypatch, [
        (None, CARD_MANDATE_ERROR),
        (None, "Razorpay request failed: something else"),
        (None, "Razorpay request failed: can't cancel"),
    ])
    ok, error = razorpay_utils.update_subscription_quantity("sub_1", 4)
    assert not ok
    assert "something else" in error


def test_other_refusals_are_not_retried(monkeypatch):
    upi = "Razorpay request failed: subscriptions cannot be updated when payment mode is UPI"
    calls = _fake_razorpay(monkeypatch, [(None, upi)])
    assert razorpay_utils.update_subscription_quantity("sub_1", 4) == (False, upi)
    assert len(calls) == 1
