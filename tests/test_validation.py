"""
tests/test_validation.py
------------------------
Unit tests for validation.py
"""

import sys
import os

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from validation import validate_initialization, validate_withdrawal


# ---------------------------------------------------------------------------
# validate_initialization — helpers
# ---------------------------------------------------------------------------

VALID_DATE    = pd.Timestamp("2026-01-15")
VALID_AGE     = 65
VALID_PREMIUM = 100_000


# ---------------------------------------------------------------------------
# validate_initialization — valid input
# ---------------------------------------------------------------------------

def test_validate_initialization_valid_inputs():
    result = validate_initialization(VALID_DATE, VALID_AGE, VALID_PREMIUM)
    assert not result.has_errors()
    assert not result.has_warnings()
    assert len(result) == 0


# ---------------------------------------------------------------------------
# validate_initialization — IssueDate errors
# ---------------------------------------------------------------------------

def test_validate_initialization_none_issue_date_is_error():
    result = validate_initialization(None, VALID_AGE, VALID_PREMIUM)
    assert result.has_errors()

def test_validate_initialization_exceeds_upper_bound_is_error():
    result = validate_initialization(pd.Timestamp("2099-12-31"), VALID_AGE, VALID_PREMIUM)
    assert result.has_errors()
    assert "IssueDate" in dict(result.errors())

def test_validate_initialization_before_lower_bound_is_error():
    result = validate_initialization(pd.Timestamp("2019-12-31"), VALID_AGE, VALID_PREMIUM)
    assert result.has_errors()

def test_validate_initialization_on_boundary_is_valid():
    result = validate_initialization(pd.Timestamp("2020-01-01"), VALID_AGE, VALID_PREMIUM)
    assert not result.has_errors()


# ---------------------------------------------------------------------------
# validate_initialization — IssueAge warnings
# ---------------------------------------------------------------------------

def test_validate_initialization_issue_age_96_is_warning():
    result = validate_initialization(VALID_DATE, 96, VALID_PREMIUM)
    assert not result.has_errors()
    assert result.has_warnings()
    assert "IssueAge" in dict(result.warnings())

def test_validate_initialization_issue_age_0_is_valid():
    result = validate_initialization(VALID_DATE, 0, VALID_PREMIUM)
    assert not result.has_warnings()

def test_validate_initialization_issue_age_95_is_valid():
    result = validate_initialization(VALID_DATE, 95, VALID_PREMIUM)
    assert not result.has_warnings()

def test_validate_initialization_none_issue_age_is_warning():
    result = validate_initialization(VALID_DATE, None, VALID_PREMIUM)
    assert not result.has_errors()
    assert result.has_warnings()
    assert "IssueAge" in dict(result.warnings())


# ---------------------------------------------------------------------------
# validate_initialization — SinglePremium warnings
# ---------------------------------------------------------------------------

def test_validate_initialization_lower_than_boundary_is_warning():
    result = validate_initialization(VALID_DATE, VALID_AGE, 9_999)
    assert not result.has_errors()
    assert result.has_warnings()
    assert "SinglePremium" in dict(result.warnings())

def test_validate_initialization_premium_higher_than_boundary_is_warning():
    result = validate_initialization(VALID_DATE, VALID_AGE, 1_000_001)
    assert result.has_warnings()
    assert "SinglePremium" in dict(result.warnings())

def test_validate_initialization_boundary_10000_is_valid():
    result = validate_initialization(VALID_DATE, VALID_AGE, 10_000)
    assert not result.has_warnings()

def test_validate_initialization_boundary_1000000_is_valid():
    result = validate_initialization(VALID_DATE, VALID_AGE, 1_000_000)
    assert not result.has_warnings()

# ---------------------------------------------------------------------------
# validate_initialization — Multiple issues can coexist
# ---------------------------------------------------------------------------

def test_validate_initialization_error_and_warning():
    result = validate_initialization(pd.Timestamp("2099-01-01"), VALID_AGE, 500)
    assert result.has_errors()
    assert result.has_warnings()

# ---------------------------------------------------------------------------
# validate_initialization — AccumulatedInterestCurrentYear warnings
# ---------------------------------------------------------------------------s

def test_validate_initialization_valid_accumulated_interest():
    result = validate_initialization(VALID_DATE, VALID_AGE, VALID_PREMIUM, 50_000)
    assert "AccumulatedInterestCurrentYear" not in dict(result.warnings())

def test_validate_initialization_accumulated_interest_current_year_below_range_warns():
    result = validate_initialization(VALID_DATE, VALID_AGE, VALID_PREMIUM, 5_000)
    assert result.has_warnings()
    assert "AccumulatedInterestCurrentYear" in dict(result.warnings())

def test_validate_initialization_accumulated_interest_current_year_above_range_warns():
    result = validate_initialization(VALID_DATE, VALID_AGE, VALID_PREMIUM, 1_000_001)
    assert result.has_warnings()
    assert "AccumulatedInterestCurrentYear" in dict(result.warnings())
# ---------------------------------------------------------------------------
# validate_withdrawal — valid input
# ---------------------------------------------------------------------------

def test_validate_withdrawal_valid_input():
    result = validate_withdrawal(3_000, 100_000, 5_000, event_date_provided=True)
    assert not result.has_errors()
    assert not result.has_warnings()


# ---------------------------------------------------------------------------
# validate_withdrawal — missing event date
# ---------------------------------------------------------------------------

def test_validate_withdrawal_missing_event_date_is_warning():
    result = validate_withdrawal(3_000, 100_000, 5_000, event_date_provided=False)
    assert not result.has_errors()
    assert result.has_warnings()
    assert "ValuationDate" in dict(result.warnings())


# ---------------------------------------------------------------------------
# validate_withdrawal — GrossWD exceeds AccountValue
# ---------------------------------------------------------------------------

def test_validate_withdrawal_wd_exceeds_av_is_error():
    result = validate_withdrawal(110_000, 100_000, 50_000, event_date_provided=True)
    assert result.has_errors()
    assert "GrossWD" in dict(result.errors())

def test_validate_withdrawal_wd_exactly_equals_av_is_not_error():
    result = validate_withdrawal(100_000, 100_000, 50_000, event_date_provided=True)
    assert not result.has_errors()


# ---------------------------------------------------------------------------
# validate_withdrawal — GrossWD exceeds PenaltyFreeWithdrawalBalance (warning only)
# ---------------------------------------------------------------------------

def test_validate_withdrawal_wd_exceeds_pfwb_is_warning():
    result = validate_withdrawal(6_000, 100_000, 5_000, event_date_provided=True)
    assert not result.has_errors()
    assert result.has_warnings()
    assert "GrossWD" in dict(result.warnings())

def test_validate_withdrawal_wd_within_pfwb_is_valid():
    result = validate_withdrawal(4_000, 100_000, 5_000, event_date_provided=True)
    assert not result.has_warnings()

def test_validate_withdrawal_wd_av_error_prevents_pfwb_warning():
    # When WD > AV the fatal error fires; the PFWB branch is skipped
    result = validate_withdrawal(110_000, 100_000, 5_000, event_date_provided=True)
    assert result.has_errors()
    pfwb_warnings = [f for f, _ in result.warnings() if "PFWB" in f]
    assert pfwb_warnings == []
