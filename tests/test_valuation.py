"""
tests/test_valuation.py
-----------------------
Unit tests for valuation.py (roll_forward).
"""

import sys
import os

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from valuation import roll_forward
from tests.sample_data import sc_table, CREDIT_RATE, SINGLE_PREMIUM, ISSUE_DATE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sc():
    return sc_table()


def eod(val_date=ISSUE_DATE, av=SINGLE_PREMIUM, cr=CREDIT_RATE) -> dict:
    """Minimal EOD state dict for roll-forward testing."""
    return {
        "ValuationDate":                   pd.Timestamp(val_date),
        "Event":                           "PolicyIssue",
        "AccountValue":                    av,
        "CurrentCreditRate":               cr,
        "AccumulatedInterestCurrentYear":  0.0,
        "PenaltyFreeWithdrawalBalance":    0.0,
        "IssueDate":                       pd.Timestamp(ISSUE_DATE),
        "GuaranteePeriodEndDate":          pd.Timestamp("2031-01-15"),
        "GuaranteePeriodStartDate":        pd.Timestamp(ISSUE_DATE),
        "PolicyNumber":                    "TEST-001",
        "ProductType":                     "5",
        "PlanCode":                        "5",
        "IssueAge":                        65,
        "State":                           "TX",
        "SinglePremium":                   SINGLE_PREMIUM,
        "SelectedRiders":                  None,
        "GuaranteedMinimumInterestRate":   0.01,
        "NonforfeitureRate":               0.024,
        "MaturityDate":                    pd.Timestamp("2061-01-15"),
        "AnnuitantDOB":                    pd.Timestamp("1960-03-01"),
        "OwnerDOB":                        pd.Timestamp("1962-07-04"),
        "PremiumTaxRate":                  0.0,
        "MVAReferenceRateAtStart":         0.042,
        "_cc":                             0.0,
    }


# ---------------------------------------------------------------------------
# Event label and date
# ---------------------------------------------------------------------------

def test_roll_forward_event_label_is_valuation(sc):
    result = roll_forward(eod(), sc, "2026-02-15")
    assert result["Event"] == "Valuation"

def test_roll_forward_valuation_date_updated(sc):
    result = roll_forward(eod(), sc, "2026-06-15")
    assert result["ValuationDate"] == pd.Timestamp("2026-06-15")

def test_roll_forward_defaults_to_next_day(sc):
    result = roll_forward(eod(), sc)
    assert result["ValuationDate"] == pd.Timestamp("2026-01-16")


# ---------------------------------------------------------------------------
# Transaction fields cleared
# ---------------------------------------------------------------------------

def test_roll_forward_transaction_fields_are_none(sc):
    result = roll_forward(eod(), sc, "2026-06-15")
    assert result.get("GrossWD") is None
    assert result.get("Net") is None
    assert result.get("Tax") is None


# ---------------------------------------------------------------------------
# Static field carry-forward
# ---------------------------------------------------------------------------

def test_roll_forward_policy_number_carried(sc):
    result = roll_forward(eod(), sc, "2026-06-15")
    assert result["PolicyNumber"] == "TEST-001"

def test_roll_forward_state_carried(sc):
    result = roll_forward(eod(), sc, "2026-06-15")
    assert result["State"] == "TX"


# ---------------------------------------------------------------------------
# Snapshot keys
# ---------------------------------------------------------------------------

def test_roll_forward_snapshot_keys_present(sc):
    result = roll_forward(eod(), sc, "2026-06-15")
    for key in ("SurrenderChargeRate", "SurrenderCharge", "MVA",
                "CashSurrenderValue", "RemainingMonthsInGuaranteePeriod"):
        assert key in result


# ---------------------------------------------------------------------------
# Interest accrual
# ---------------------------------------------------------------------------

def test_roll_forward_zero_rate_no_growth(sc):
    result = roll_forward(eod(cr=0.0), sc, "2026-06-15")
    assert result["AccountValue"] == pytest.approx(100_000.0, abs=1e-4)

def test_roll_forward_positive_rate_grows_av(sc):
    result = roll_forward(eod(), sc, "2026-06-15")
    assert result["AccountValue"] > 100_000.0

def test_roll_forward_av_on_next_date(sc):
    result = roll_forward(eod(), sc, "2026-01-16")
    expected = 100_000 * (1 + CREDIT_RATE) ** (1 / 365)
    assert result["AccountValue"] == pytest.approx(expected, abs=0.01)

def test_roll_forward_av_after_one_full_year(sc):
    result = roll_forward(eod(), sc, "2027-01-15")
    expected = 100_000 * (1 + CREDIT_RATE) ** (365 / 365)
    assert result["AccountValue"] == pytest.approx(expected, abs=0.01)

def test_roll_forward_daily_interest_is_positive(sc):
    result = roll_forward(eod(), sc, "2026-06-15")
    assert result["DailyInterest"] > 0.0

def test_roll_forward_daily_interest_equals_av_change(sc):
    result = roll_forward(eod(), sc, "2026-06-15")
    assert result["DailyInterest"] == pytest.approx(
        result["AccountValue"] - 100_000, rel=1e-6
    )


# ---------------------------------------------------------------------------
# Anniversary reset of AccumulatedInterestCurrentYear
# ---------------------------------------------------------------------------

def test_roll_forward_accumulates_before_anniversary(sc):
    result = roll_forward(eod(), sc, "2026-07-15")
    assert result["AccumulatedInterestCurrentYear"] > 0.0

def test_roll_forward_resets_on_anniversary(sc):
    # Exactly the first anniversary (2027-01-15)
    result = roll_forward(eod(), sc, "2027-01-15")
    assert result["AccumulatedInterestCurrentYear"] == pytest.approx(
        result["DailyInterest"], rel=1e-6
    )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_roll_forward_zero_days_no_growth(sc):
    result = roll_forward(eod(), sc, ISSUE_DATE)
    assert result["AccountValue"] == pytest.approx(100_000.0, rel=1e-6)
