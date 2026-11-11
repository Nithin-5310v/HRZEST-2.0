"""update_subscription_quantity(): a new headcount is always scheduled for the
cycle end -- charged from the next billing date, never "now" (Razorpay's
immediate, prorated charge would bill a new employee whose month was already
paid at registration a second time)."""
from utils import razorpay_utils


def _fake_razorpay(monkeypatch, responses):
    """Each call pops the next (data, error) from responses; returns the call log."""
    calls = []

    def fake_request(path, body=None, method="POST"):
        calls.append((path, body, method))
        return responses.pop(0)

    monkeypatch.setattr(razorpay_utils, "_request", fake_request)
    return calls


def test_change_is_scheduled_for_the_next_charge(monkeypatch):
    calls = _fake_razorpay(monkeypatch, [({"id": "sub_1"}, None)])
    assert razorpay_utils.update_subscription_quantity("sub_1", 4) == (True, None)
    assert calls == [("/subscriptions/sub_1", {"quantity": 4, "schedule_change_at": "cycle_end"}, "PATCH")]


def test_never_asks_for_an_immediate_change(monkeypatch):
    calls = _fake_razorpay(monkeypatch, [({"id": "sub_1"}, None)])
    razorpay_utils.update_subscription_quantity("sub_1", 7)
    assert all(body is None or body.get("schedule_change_at") != "now" for _, body, _ in calls)


def test_replaces_an_earlier_scheduled_change(monkeypatch):
    calls = _fake_razorpay(monkeypatch, [
        (None, "Razorpay request failed: subscription already has a scheduled change"),
        ({}, None),  # cancel_scheduled_changes
        ({"id": "sub_1"}, None),
    ])
    assert razorpay_utils.update_subscription_quantity("sub_1", 5) == (True, None)
    assert calls[1] == ("/subscriptions/sub_1/cancel_scheduled_changes", None, "POST")
    assert calls[2] == ("/subscriptions/sub_1", {"quantity": 5, "schedule_change_at": "cycle_end"}, "PATCH")


def test_reports_failure_when_scheduling_fails(monkeypatch):
    _fake_razorpay(monkeypatch, [
        (None, "Razorpay request failed: something else"),
        (None, "Razorpay request failed: can't cancel"),
    ])
    ok, error = razorpay_utils.update_subscription_quantity("sub_1", 4)
    assert not ok
    assert "something else" in error


def test_quantity_is_at_least_one(monkeypatch):
    calls = _fake_razorpay(monkeypatch, [({"id": "sub_1"}, None)])
    razorpay_utils.update_subscription_quantity("sub_1", 0)
    assert calls[0][1]["quantity"] == 1


def test_fixed_amount_refusals_are_returned_without_cancelling_anything(monkeypatch):
    """UPI / a card issued in India can't change at all: the caller sets
    Auto-Debit up again, so nothing scheduled is cancelled."""
    for refusal in ("Razorpay request failed: subscriptions cannot be updated when payment mode is UPI",
                    "Razorpay request failed: Only offers can be updated for subscriptions "
                    "when payment mode is domestic card."):
        calls = _fake_razorpay(monkeypatch, [(None, refusal)])
        assert razorpay_utils.update_subscription_quantity("sub_1", 4) == (False, refusal)
        assert len(calls) == 1
