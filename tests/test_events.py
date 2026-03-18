"""
tests/test_events_edge.py
-------------------------
Edge-case unit tests for events/even1.py and events/event_2.py.
"""

from __future__ import annotations

import pandas as pd
import pytest

from events.event_1 import process_initialization
from events.event_2 import extract_event2_input, process_withdrawal
from config import GMIR, NONFORFEITURE, MVA_REF_RATE, PREMIUM_TAX_RATE
from tests.sample_data import (
    policy_row,
    sc_table,
    product_tables,
    SINGLE_PREMIUM,
    ISSUE_DATE,
    GP_END,
    WD_VALUATION_DATE,
)


# ---------------------------------------------------------------------------
# Re-usable setup helpers
# ---------------------------------------------------------------------------

def a_standard_policy_row(**overrides) -> pd.Series:
    """
    Return a normal, fully-filled-in policy row.
    """
    return policy_row(**overrides)


def a_policy_snapshot(
    account_value: float = 100_000.0,
    penalty_free_balance: float = 10_000.0,
) -> dict:
    """
    Return a minimal snapshot of a policy's financial state at a point in time.
    The snapshot includes the key fields needed to process a withdrawal.
    """
    return {
        "ValuationDate": pd.Timestamp(WD_VALUATION_DATE),
        "AccountValue": account_value,
        "PenaltyFreeWithdrawalBalance": penalty_free_balance,
        "IssueDate": pd.Timestamp(ISSUE_DATE),
        "GuaranteePeriodEndDate": pd.Timestamp(GP_END),
        "_cc": 0.0,         # Internal annual contract charge (zero for now)
    }


# ===========================================================================
# EVENT 1 TESTS
# ===========================================================================


class TestMissingDatesGetFilledInAutomatically:
    """
    When certain dates are not provided on the input form, the system
    should fill them in with sensible defaults rather than crashing.

    Covers: guarantee period start/end dates and the contract maturity date.
    """

    def test_guarantee_start_date_defaults_to_issue_date_when_blank(self):
        surrender_charge_table = sc_table()
        rates_table = product_tables()

        # Drop the GuaranteePeriodStartDate from the input to simulate it being left blank
        policy = a_standard_policy_row()
        if "GuaranteePeriodStartDate" in policy.index:
            policy = policy.drop("GuaranteePeriodStartDate")

        result = process_initialization(policy, surrender_charge_table, rates_table)

        # The system should have filled in the issue date as the start date
        assert result.eod["GuaranteePeriodStartDate"] == pd.Timestamp(ISSUE_DATE)

    def test_guarantee_end_date_provided_by_user_is_not_changed(self):
        """
        If the user explicitly typed a Guarantee Period End Date,
        the system must use it — it should NOT recalculate and overwrite it.
        """
        surrender_charge_table = sc_table()
        rates_table = product_tables()

        # The user manually set the guarantee end to a specific date
        user_provided_end_date = pd.Timestamp("2030-06-15")

        policy = a_standard_policy_row()
        policy["GuaranteePeriodEndDate"] = user_provided_end_date

        result = process_initialization(policy, surrender_charge_table, rates_table)

        # The system should have kept the user's provided end date
        assert result.eod["GuaranteePeriodEndDate"] == user_provided_end_date

    def test_maturity_date_provided_by_user_is_not_changed(self):
        """
        If the user explicitly typed a Maturity Date, the system must
        use it and not recalculate it from the annuitant's date of birth.
        """
        surrender_charge_table = sc_table()
        rates_table = product_tables()

        # The user manually set the maturity date
        user_provided_maturity_date = pd.Timestamp("2055-01-01")

        policy = a_standard_policy_row()
        policy["MaturityDate"] = user_provided_maturity_date

        result = process_initialization(policy, surrender_charge_table, rates_table)

        # The system should have kept the user's provided maturity date
        assert result.eod["MaturityDate"] == user_provided_maturity_date


class TestContractLengthIsWorkedOutCorrectly:
    """
    Policies come in different lengths (3, 5, 7, 10 years).
 
    ProductType arrives as a plain number ("3", "5", "7", "10") and is
    tried first.  PlanCode arrives as "MYGA_5" or "FIA_10" — the system
    extracts the trailing number to determine the guarantee term length.
 
    These tests check both paths and the fallback when neither is recognised.
    """
 
    def test_product_type_plain_number_sets_correct_term(self):
        """
        ProductType = '7' (a plain number) should produce a 7-year term
        without needing to look at PlanCode at all.
        """
        surrender_charge_table = sc_table()
        rates_table = product_tables()
 
        policy = a_standard_policy_row()
        policy["ProductType"] = "7"
 
        result = process_initialization(policy, surrender_charge_table, rates_table)
 
        guarantee_start = result.eod["GuaranteePeriodStartDate"]
        guarantee_end   = result.eod["GuaranteePeriodEndDate"]
        length_in_years = (guarantee_end - guarantee_start).days / 365
 
        assert length_in_years == pytest.approx(7.0, abs=0.1)
 
    @pytest.mark.parametrize("plan_code, expected_years", [
        ("MYGA_5",  5),
        ("FIA_10", 10),
        ("MYGA_3",  3),
        ("FIA_7",   7),
    ])
    def test_plan_code_trailing_number_drives_guarantee_term(self, plan_code, expected_years):
        """
        When ProductType is unrecognised, the system should extract the
        trailing number from PlanCode (e.g. 'MYGA_5' → '5', 'FIA_10' → '10')
        and use that to set the guarantee term length.
        """
        surrender_charge_table = sc_table()
        rates_table = product_tables()
 
        policy = a_standard_policy_row()
        policy["ProductType"] = "UNKNOWN"   # force the fallback to PlanCode
        policy["PlanCode"]    = plan_code
 
        result = process_initialization(policy, surrender_charge_table, rates_table)
 
        guarantee_start = result.eod["GuaranteePeriodStartDate"]
        guarantee_end   = result.eod["GuaranteePeriodEndDate"]
        length_in_years = (guarantee_end - guarantee_start).days / 365
 
        assert length_in_years == pytest.approx(expected_years, abs=0.1)
 
    def test_completely_unrecognised_codes_fall_back_to_5_year_default(self):
        """
        If neither ProductType nor PlanCode contains a recognisable number,
        the system should safely default to a 5-year guarantee term
        rather than crashing.
        """
        surrender_charge_table = sc_table()
        rates_table = product_tables()
 
        policy = a_standard_policy_row(product_type="UNKNOWN")
        policy["PlanCode"] = "MYGA_UNKNOWN"   # no trailing number to parse
 
        result = process_initialization(policy, surrender_charge_table, rates_table)
 
        length_in_years = (
            result.eod["GuaranteePeriodEndDate"] - result.eod["GuaranteePeriodStartDate"]
        ).days / 365
 
        assert length_in_years == pytest.approx(5.0, abs=0.1)


class TestInterestAndTaxRatesFallBackToSystemDefaults:
    """
    Several rates (minimum interest rate, tax rate, etc.) can be
    supplied on the input form, but if they are left blank the system
    should quietly use the company-wide default values from the config file.
    """

    policy = a_standard_policy_row()

    def test_minimum_interest_rate_uses_company_default_when_blank(self):
        """
        Guaranteed Minimum Interest Rate not on form
        → system uses the company-wide GMIR constant.
        """
        policy = a_standard_policy_row().drop("GuaranteedMinimumInterestRate", errors="ignore") 
        result = process_initialization(policy, sc_table(), product_tables())
        assert result.eod["GuaranteedMinimumInterestRate"] == pytest.approx(GMIR)

    def test_nonforfeiture_rate_uses_company_default_when_blank(self):
        """
        Nonforfeiture Rate not on form
        → system uses the company-wide NONFORFEITURE constant.
        """
        policy =  a_standard_policy_row().drop("NonforfeitureRate", errors="ignore")
        result = process_initialization(policy, sc_table(), product_tables())
        assert result.eod["NonforfeitureRate"] == pytest.approx(NONFORFEITURE)

    def test_premium_tax_rate_uses_company_default_when_blank(self):
        """
        Premium Tax Rate not on form
        → system uses the company-wide PREMIUM_TAX_RATE constant.
        """
        policy =  a_standard_policy_row().drop("PremiumTaxRate", errors="ignore")
        result = process_initialization(policy, sc_table(), product_tables())
        assert result.eod["PremiumTaxRate"] == pytest.approx(PREMIUM_TAX_RATE)

    def test_mva_reference_rate_uses_company_default_when_blank(self):
        """
        MVA Reference Rate not on form
        → system uses the company-wide MVA_REF_RATE constant.
        """
        policy =  a_standard_policy_row().drop("MVAReferenceRateAtStart", errors="ignore")
        result = process_initialization(policy, sc_table(), product_tables())
        assert result.eod["MVAReferenceRateAtStart"] == pytest.approx(MVA_REF_RATE)


class TestStartingBalancesFallBackToSensibleDefaults:

    def test_account_value_starts_at_premium_when_not_explicitly_set(self):
        """
        If no Account Value is on the form, the system should set it equal to the Single Premium paid.
        """
        policy = a_standard_policy_row()
        if "AccountValue" in policy.index:
            policy = policy.drop("AccountValue")

        result = process_initialization(policy, sc_table(), product_tables())

        assert result.eod["AccountValue"] == pytest.approx(SINGLE_PREMIUM)

    def test_penalty_free_balance_starts_at_zero_when_not_set(self):
        """
        If no Penalty-Free Withdrawal Balance is on the form,
        the system should start it at $0 rather than crashing.
        """
        policy = a_standard_policy_row()
        if "PenaltyFreeWithdrawalBalance" in policy.index:
            policy = policy.drop("PenaltyFreeWithdrawalBalance")

        result = process_initialization(policy, sc_table(), product_tables())

        assert result.eod["PenaltyFreeWithdrawalBalance"] == pytest.approx(0.0)

    def test_accumulated_interest_starts_at_zero_when_not_set(self):
        """
        If no Accumulated Interest figure is on the form,
        the system should start it at $0.
        """
        policy = a_standard_policy_row()
        if "AccumulatedInterestCurrentYear" in policy.index:
            policy = policy.drop("AccumulatedInterestCurrentYear")

        result = process_initialization(policy, sc_table(), product_tables())

        assert result.eod["AccumulatedInterestCurrentYear"] == pytest.approx(0.0)

# ===========================================================================
# EVENT 2 TESTS
# ===========================================================================


class TestWithdrawalDetectionFromInputColumns:
    """
    Before processing a withdrawal, the system first scans the input row
    to decide WHETHER a withdrawal was requested at all, and if so,
    picks up the correct dollar amount and date.
    """

    def test_net_amount_and_tax_amount_are_passed_through_when_present(self):
        """
        If Net and Tax columns are filled in on the input row, those values
        should be forwarded into the withdrawal event unchanged.
        """
        policy = a_standard_policy_row()
        policy["Gross WD"] = 5_000.0
        policy["Valuation Date"] = WD_VALUATION_DATE
        policy["Net"] = 4_500.0
        policy["Tax"] = 500.0

        detected_withdrawal = extract_event2_input(policy)

        assert detected_withdrawal is not None
        assert float(detected_withdrawal["Net"]) == pytest.approx(4_500.0)
        assert float(detected_withdrawal["Tax"]) == pytest.approx(500.0)


class TestWithdrawalArithmeticBoundaries:

    def test_withdrawing_every_dollar_leaves_account_at_zero_without_error(self):
        """
        A customer is allowed to withdraw their entire account balance.
        After doing so, the account value should be exactly $0.00
        and the system should NOT raise an error.
        """
        full_balance = 50_000.0

        # Set penalty-free balance equal to full balance so no penalty warning fires
        policy_snapshot = a_policy_snapshot(
            account_value=full_balance,
            penalty_free_balance=full_balance,
        )

        withdrawal_request = {
            "Gross WD":       full_balance,        # withdraw every dollar
            "Valuation Date": WD_VALUATION_DATE,
        }

        result = process_withdrawal(policy_snapshot, withdrawal_request, sc_table())

        assert result.eod["AccountValue"] == pytest.approx(0.0)
        assert not result.validation.has_errors()

    def test_withdrawing_more_than_penalty_free_balance_clamps_that_balance_to_zero(self):
        """
        If the withdrawal amount exceeds the penalty-free balance
        (but is still within the overall account value), the remaining
        penalty-free balance must floor at $0 — it cannot go negative.

        Example: PFWB = $3,000 but customer withdraws $5,000.
        After the withdrawal: PFWB should be $0.00, not -$2,000.
        """
        policy_snapshot = a_policy_snapshot(
            account_value=100_000.0,
            penalty_free_balance=3_000.0,   # customer can take $3k without penalty
        )

        withdrawal_request = {
            "Gross WD":       5_000.0,      # more than the penalty-free amount
            "Valuation Date": WD_VALUATION_DATE,
        }

        result = process_withdrawal(policy_snapshot, withdrawal_request, sc_table())

        assert result.eod["PenaltyFreeWithdrawalBalance"] == pytest.approx(0.0)