from app.services.payment_policy import decide_payment_action


def test_small_amount_auto_pays():
    result = decide_payment_action(
        owed_amount=25,
        already_auto_paid_today=False,
    )

    assert result.action == "AUTO_PAY"
    assert result.amount == 25


def test_large_amount_needs_approval():
    result = decide_payment_action(
        owed_amount=425,
        already_auto_paid_today=False,
    )

    assert result.action == "REQUIRE_APPROVAL"


def test_second_payment_same_day_needs_approval():
    result = decide_payment_action(
        owed_amount=10,
        already_auto_paid_today=True,
    )

    assert result.action == "REQUIRE_APPROVAL"


def test_zero_owed_does_nothing():
    result = decide_payment_action(
        owed_amount=0,
        already_auto_paid_today=False,
    )

    assert result.action == "NO_ACTION"