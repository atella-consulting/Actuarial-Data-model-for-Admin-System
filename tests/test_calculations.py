"""
tests/test_calculations.py
--------------------------
Unit tests for calculations.py
"""

import sys
import os

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from calculations import (
    policy_year,
    month_diff,
    sc_rate,
    snapshot,
    maturity_date_from_issue_and_annuitant,
)
from tests.sample_data import make_sc_table


# ---------------------------------------------------------------------------
# policy_year
# ---------------------------------------------------------------------------

def test_policy_year_for_issue_day_is_year1():
    assert policy_year("2026-01-15", "2026-01-15") == 1

def test_policy_year_day_before_first_anniversary_is_year1():
    assert policy_year("2026-01-15", "2027-01-14") == 1

def test_policy_year_first_anniversary_is_year2():
    assert policy_year("2026-01-15", "2027-01-15") == 2

def test_policy_year_missing_issue_defaults_to_1():
    assert policy_year(None, "2027-01-15") == 1

def test_policy_year_missing_val_defaults_to_1():
    assert policy_year("2026-01-15", None) == 1

def test_policy_year_minimum_is_always_1():
    # Valuation date before issue date must still return 1
    assert policy_year("2026-01-15", "2025-01-01") == 1


# ---------------------------------------------------------------------------
# month_diff
# ---------------------------------------------------------------------------

def test_month_diff_exact_months():
    assert month_diff("2026-02-01", "2027-02-01") == 12

def test_month_diff_partial_month():
    # Feb 15 → Feb 14 next year = 11 complete months
    assert month_diff("2026-02-15", "2027-02-14") == 11

def test_month_diff_same_date_is_zero():
    assert month_diff("2026-01-15", "2026-01-15") == 0

def test_month_diff_end_before_start_is_zero():
    assert month_diff("2027-01-15", "2026-01-15") == 0

def test_month_diff_missing_date_is_zero():
    assert month_diff(None, "2027-01-15") == 0

def test_month_diff_5_years_is_60_months():
    assert month_diff("2026-01-15", "2031-01-15") == 60


# ---------------------------------------------------------------------------
# sc_rate
# ---------------------------------------------------------------------------

@pytest.fixture
def sc_table():
    return make_sc_table()

def test_sc_rate_year1(sc_table):
    assert sc_rate(sc_table, 1) == pytest.approx(0.08)

def test_sc_rate_year3(sc_table):
    assert sc_rate(sc_table, 3) == pytest.approx(0.06)

def test_sc_rate_year5(sc_table):
    assert sc_rate(sc_table, 5) == pytest.approx(0.04)

def test_sc_rate_beyond_table_returns_zero(sc_table):
    assert sc_rate(sc_table, 99) == 0.0

def test_sc_rate_none_table_returns_zero():
    assert sc_rate(None, 1) == 0.0

def test_sc_rate_empty_table_returns_zero():
    empty = pd.DataFrame(columns=["Year", "ChargeRate"])
    assert sc_rate(empty, 1) == 0.0


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------

ISSUE_DT = "2026-01-15"
GP_END   = "2031-01-15"

@pytest.fixture
def snap_year1(sc_table):
    """Snapshot taken on the issue date — policy year 1."""
    return snapshot("2026-01-15", 100_000, ISSUE_DT, GP_END, sc_table)

def test_snapshot_keys_present(snap_year1):
    expected = {
        "SurrenderChargeRate",
        "SurrenderCharge",
        "MVA",
        "CashSurrenderValue",
        "RemainingMonthsInGuaranteePeriod",
    }
    assert set(snap_year1.keys()) == expected

def test_snapshot_year1_surrender_charge_rate(snap_year1):
    assert snap_year1["SurrenderChargeRate"] == pytest.approx(0.08)

def test_snapshot_year1_surrender_charge_amount(snap_year1):
    assert snap_year1["SurrenderCharge"] == pytest.approx(8_000.0)

def test_snapshot_csv_equals_av_minus_sc(snap_year1):
    expected_csv = 100_000.0 - snap_year1["SurrenderCharge"]
    assert snap_year1["CashSurrenderValue"] == pytest.approx(expected_csv)

def test_snapshot_remaining_months_at_issue(snap_year1):
    assert snap_year1["RemainingMonthsInGuaranteePeriod"] == 60

def test_snapshot_remaining_months_decreases(sc_table):
    result = snapshot("2027-01-15", 100_000, ISSUE_DT, GP_END, sc_table)
    assert result["RemainingMonthsInGuaranteePeriod"] == 48

def test_snapshot_no_sc_table_zero_charge():
    result = snapshot("2026-01-15", 100_000, ISSUE_DT, GP_END, None)
    assert result["SurrenderCharge"] == 0.0
    assert result["CashSurrenderValue"] == pytest.approx(100_000.0)


# ---------------------------------------------------------------------------
# maturity_date_from_issue_and_annuitant
# ---------------------------------------------------------------------------

def test_maturity_date_standard_case():
    # Annuitant born 1960-03-01 → age 100 on 2060-03-01
    # Issue 2026-01-15 → first anniversary on/after 2060-03-01 = 2061-01-15
    result = maturity_date_from_issue_and_annuitant("2026-01-15", "1960-03-01")
    assert result == pd.Timestamp("2061-01-15")

def test_maturity_date_anniversary_on_100th_birthday():
    # Issue on annuitant's birthday → first anniversary at age 100
    result = maturity_date_from_issue_and_annuitant("1960-03-01", "1960-03-01")
    assert result == pd.Timestamp("2060-03-01")

def test_maturity_date_missing_issue_returns_nat():
    assert pd.isna(maturity_date_from_issue_and_annuitant(None, "1960-03-01"))

def test_maturity_date_missing_dob_returns_nat():
    assert pd.isna(maturity_date_from_issue_and_annuitant("2026-01-15", None))
