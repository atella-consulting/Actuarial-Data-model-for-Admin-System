from __future__ import annotations

import pandas as pd
import pytest

from calculations import compute_mva, is_mva_waiver_window, get_mva_rate


def test_mva_positive_when_A_gt_B():
    mva = compute_mva(
        excess_amount=50_000.0,
        rate_at_start=0.06,
        rate_current=0.05,
        remaining_months=12,
    )
    assert mva > 0.0


def test_mva_negative_when_A_lt_B():
    mva = compute_mva(
        excess_amount=50_000.0,
        rate_at_start=0.05,
        rate_current=0.06,
        remaining_months=12,
    )
    assert mva < 0.0


def test_mva_zero_when_t_is_zero():
    mva = compute_mva(
        excess_amount=50_000.0,
        rate_at_start=0.05,
        rate_current=0.06,
        remaining_months=0,
    )
    assert mva == 0.0


def test_mva_zero_when_excess_amount_is_zero():
    mva = compute_mva(
        excess_amount=0.0,
        rate_at_start=0.05,
        rate_current=0.06,
        remaining_months=12,
    )
    assert mva == 0.0


def test_waiver_window_returns_true_inside_window():
    assert is_mva_waiver_window(
        val_date=pd.Timestamp("2026-02-15"),
        gp_start=pd.Timestamp("2026-02-01"),
    ) is True


def test_waiver_window_returns_false_outside_window():
    assert is_mva_waiver_window(
        val_date=pd.Timestamp("2026-03-15"),
        gp_start=pd.Timestamp("2026-02-01"),
    ) is False


def test_get_mva_rate_rolls_back_for_weekend():
    rates_df = pd.DataFrame(
        {"RATE": [0.042]},
        index=[pd.Timestamp("2026-01-30")],  # Friday
    )

    result = get_mva_rate(
        rates_df=rates_df,
        date=pd.Timestamp("2026-02-01"),     # Sunday
        column="RATE",
    )
    assert result == pytest.approx(0.042)

def test_compute_mva_matches_formula_for_case():
    """
    Excess withdrawal = 50000
    A = 5.00%
    B = 6.00%
    t = remaining_months / 12 = 5 / 12

    Expected:
        MVA = 50000 * (((1.05 / 1.06) ** (5 / 12)) - 1)
    """
    result = compute_mva(
        excess_amount=50_000.0,
        rate_at_start=0.05,   # A
        rate_current=0.06,    # B
        remaining_months=5,
    )

    expected = 50_000.0 * (((1.05 / 1.06) ** (5 / 12)) - 1)

    assert result == pytest.approx(expected, rel=1e-12)
    assert result < 0


@pytest.mark.xfail(reason="CSV floor not implemented yet")
def test_floor_prevents_csv_below_minimum_guaranteed_value():
    assert False