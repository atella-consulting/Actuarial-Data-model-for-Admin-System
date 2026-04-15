"""
tests/test_rider_fees.py
------------------------
Focused tests for SelectedRiders fee mapping and conflict warnings.
"""

from __future__ import annotations

import pandas as pd
import pytest

from calculations import (
    parse_selected_riders,
    rider_credit_rate_adjustment,
    compute_mva,
    month_diff,
)
from events.event_1 import process_initialization
from events.event_2 import process_withdrawal


def _product_tables_for_riders() -> pd.DataFrame:
    base_date = pd.Timestamp("2026-01-01")
    rows = [
        # Base policy rates
        {"TableName": "CreditingRate", "ProductType": "MYGA_05", "Value": 0.0500, "EffectiveDate": base_date},
        {"TableName": "GuaranteedMinimumInterestRate", "ProductType": "MYGA_05", "Value": 0.0100, "EffectiveDate": base_date},
        {"TableName": "NonforfeitureRate", "ProductType": "MYGA_05", "Value": 0.0240, "EffectiveDate": base_date},
        # Rider fees used for CCR reduction (RiderFee layout)
        {"TableName": "RiderFee", "ProductType": "DeathBenefit", "Value": 0.0010, "EffectiveDate": base_date},
        {"TableName": "RiderFee", "ProductType": "5FreeWD", "Value": 0.0020, "EffectiveDate": base_date},
        {"TableName": "RiderFee", "ProductType": "InterestWD", "Value": 0.0030, "EffectiveDate": base_date},
        {"TableName": "RiderFee", "ProductType": "EnhInterestWD", "Value": 0.0040, "EffectiveDate": base_date},
        {"TableName": "RiderFee", "ProductType": "EnhBenefitWD", "Value": 0.0050, "EffectiveDate": base_date},
        # LBR / ELBR withdrawal-limit percentages
        {"TableName": "FreeWD", "ProductType": "LiquidityBenefitWD", "Value": 0.10, "EffectiveDate": base_date},
        {"TableName": "FreeWD", "ProductType": "EnhLiquidityBenefitWD", "Value": 0.20, "EffectiveDate": base_date},
    ]
    return pd.DataFrame(rows)


def _minimal_policy_row(selected_riders: str) -> pd.Series:
    return pd.Series(
        {
            "PolicyNumber": "RIDER-001",
            "IssueDate": "2026-01-15",
            "Valuation Date": "2026-01-15",
            "AnnuitantDOB": "1960-03-01",
            "OwnerDOB": "1962-07-04",
            "SinglePremium": 100000.0,
            "ProductType": "MYGA_05",
            "PlanCode": "MYGA_05",
            "State": "TX",
            "SelectedRiders": selected_riders,
            "AccountValue": 100000.0,
            "AccumulatedInterestCurrentYear": 0.0,
            "PenaltyFreeWithdrawalBalance": 0.0,
        }
    )


def _sc_table_standard() -> pd.DataFrame:
    return pd.DataFrame([{"Year": 1, "ChargeRate": 0.08}])


def _mva_rates_for_event2() -> pd.DataFrame:
    return pd.DataFrame(
        {"Y05": [0.043]},
        index=pd.to_datetime(["2026-06-14"]),
    )


def _sc_table_year1_year2() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Year": 1, "ChargeRate": 0.08},
            {"Year": 2, "ChargeRate": 0.07},
        ]
    )


def _mva_rates_for_event2_with_year2() -> pd.DataFrame:
    return pd.DataFrame(
        {"Y05": [0.043, 0.043]},
        index=pd.to_datetime(["2026-06-14", "2027-06-14"]),
    )


def test_parse_selected_riders_normalizes_and_deduplicates():
    riders = parse_selected_riders(" DBR, iwr, 5wr, DBR , ")
    assert riders == ["DBR", "IWR", "5WR"]


def test_rider_credit_rate_adjustment_applies_mapping_and_placeholders():
    adj = rider_credit_rate_adjustment(
        product_tables=_product_tables_for_riders(),
        product_type="MYGA_05",
        valuation_date="2026-01-15",
        selected_riders="DBR, 5WR, IWR, EIWR, LBR, ELBR",
    )

    # EIWR and LBR are placeholders (not applied yet).
    expected = 0.0010 + 0.0020 + 0.0030 + 0.0050
    assert adj["total_fee"] == pytest.approx(expected)


def test_process_initialization_reduces_current_credit_rate_by_selected_riders():
    result = process_initialization(
        row=_minimal_policy_row("DBR, 5WR"),
        sc_tbl=pd.DataFrame(columns=["Year", "ChargeRate"]),
        product_tables=_product_tables_for_riders(),
        rates_df=None,
        rmd_table=None,
    )

    # Base CCR 5.00% reduced by DBR 0.10% and 5WR 0.20%
    assert result.eod["CurrentCreditRate"] == pytest.approx(0.0500 - 0.0010 - 0.0020)


def test_process_initialization_calculates_penalty_free_amount_fields_and_ignores_inputs():
    policy = _minimal_policy_row("LBR, ELBR")
    policy["PenaltyFreeWithdrawalAmount"] = 12_345.0
    policy["EnhancedPenaltyFreeWithdrawalAmount"] = 54_321.0

    result = process_initialization(
        row=policy,
        sc_tbl=pd.DataFrame(columns=["Year", "ChargeRate"]),
        product_tables=_product_tables_for_riders(),
        rates_df=None,
        rmd_table=None,
    )

    assert result.eod["PenaltyFreeWithdrawalAmount"] == pytest.approx(10_000.0)
    assert result.eod["EnhancedPenaltyFreeWithdrawalAmount"] == pytest.approx(20_000.0)


def test_process_initialization_warns_when_elbr_and_lbr_selected_together():
    result = process_initialization(
        row=_minimal_policy_row("ELBR, LBR"),
        sc_tbl=pd.DataFrame(columns=["Year", "ChargeRate"]),
        product_tables=_product_tables_for_riders(),
        rates_df=None,
        rmd_table=None,
    )

    warnings = dict(result.validation.warnings())
    assert "SelectedRiders" in warnings
    assert "Only one WD rider can be selected from: IWR, LBR, ELBR" in warnings["SelectedRiders"]


def test_process_initialization_warns_when_iwr_and_lbr_selected_together():
    result = process_initialization(
        row=_minimal_policy_row("IWR, LBR"),
        sc_tbl=pd.DataFrame(columns=["Year", "ChargeRate"]),
        product_tables=_product_tables_for_riders(),
        rates_df=None,
        rmd_table=None,
    )

    warnings = dict(result.validation.warnings())
    assert "SelectedRiders" in warnings
    assert "Only one WD rider can be selected from: IWR, LBR, ELBR" in warnings["SelectedRiders"]

    # LBR is placeholder-only for now, so only IWR fee is applied.
    assert result.eod["CurrentCreditRate"] == pytest.approx(0.0500 - 0.0030)


def test_process_initialization_allows_dbr_with_one_wd_rider():
    result = process_initialization(
        row=_minimal_policy_row("DBR, IWR"),
        sc_tbl=pd.DataFrame(columns=["Year", "ChargeRate"]),
        product_tables=_product_tables_for_riders(),
        rates_df=None,
        rmd_table=None,
    )

    warnings = dict(result.validation.warnings())
    assert "SelectedRiders" not in warnings
    # Base 5.00% minus DBR 0.10% and IWR 0.30%
    assert result.eod["CurrentCreditRate"] == pytest.approx(0.0500 - 0.0010 - 0.0030)


def test_process_initialization_allows_eiwr_with_iwr_under_new_wd_group_rule():
    result = process_initialization(
        row=_minimal_policy_row("EIWR, IWR"),
        sc_tbl=pd.DataFrame(columns=["Year", "ChargeRate"]),
        product_tables=_product_tables_for_riders(),
        rates_df=None,
        rmd_table=None,
    )

    warnings = dict(result.validation.warnings())
    assert "SelectedRiders" not in warnings
    # EIWR remains placeholder-only for fee application.
    assert result.eod["CurrentCreditRate"] == pytest.approx(0.0500 - 0.0030)


def test_process_initialization_reads_input_total_riders_rate_when_provided():
    policy = _minimal_policy_row("DBR, 5WR")
    policy["TotalRidersRate"] = "0.40%"

    result = process_initialization(
        row=policy,
        sc_tbl=pd.DataFrame(columns=["Year", "ChargeRate"]),
        product_tables=_product_tables_for_riders(),
        rates_df=None,
        rmd_table=None,
    )

    assert result.eod["TotalRidersRate"] == pytest.approx(0.0040)
    assert result.eod["CurrentCreditRate"] == pytest.approx(0.0500 - 0.0040)


def test_death_benefit_amount_equals_account_value_when_dbr_selected():
    result = process_initialization(
        row=_minimal_policy_row("DBR, 5WR"),
        sc_tbl=_sc_table_standard(),
        product_tables=_product_tables_for_riders(),
        rates_df=None,
        rmd_table=None,
    )
    assert result.eod["Death_Benefit_Amount"] == pytest.approx(result.eod["AccountValue"])


def test_death_benefit_amount_equals_csv_when_dbr_not_selected():
    result = process_initialization(
        row=_minimal_policy_row("5WR"),
        sc_tbl=_sc_table_standard(),
        product_tables=_product_tables_for_riders(),
        rates_df=None,
        rmd_table=None,
    )
    assert result.eod["Death_Benefit_Amount"] == pytest.approx(result.eod["CashSurrenderValue"])
    assert result.eod["Death_Benefit_Amount"] < result.eod["AccountValue"]


def test_event2_death_benefit_amount_uses_csv_without_dbr_even_with_mva():
    val_state = {
        "ValuationDate": pd.Timestamp("2026-06-15"),
        "AccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 5_000.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "5WR",
    }
    event_input = {"Gross WD": 10_000.0, "Valuation Date": "2026-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_standard(),
        rates_df=_mva_rates_for_event2(),
    )

    assert result.eod["MVA"] != 0.0
    assert result.eod["Death_Benefit_Amount"] == pytest.approx(result.eod["CashSurrenderValue"])


def test_event2_death_benefit_amount_uses_account_value_with_dbr_even_with_mva():
    val_state = {
        "ValuationDate": pd.Timestamp("2026-06-15"),
        "AccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 5_000.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "DBR, 5WR",
    }
    event_input = {"Gross WD": 10_000.0, "Valuation Date": "2026-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_standard(),
        rates_df=_mva_rates_for_event2(),
    )

    assert result.eod["MVA"] != 0.0
    assert result.eod["Death_Benefit_Amount"] == pytest.approx(result.eod["AccountValue"])


def test_iwr_waives_surrender_charge_and_mva_within_free_amount_in_policy_year_2():
    val_state = {
        "ValuationDate": pd.Timestamp("2027-06-15"),
        "AccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 0.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "IWR",
        "AccumulatedInterestCurrentYear": 3_000.0,
        "RMD_Qualified": "N",
        "RMD": 0.0,
    }
    event_input = {"Gross WD": 2_500.0, "Valuation Date": "2027-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_year1_year2(),
        rates_df=_mva_rates_for_event2_with_year2(),
    )

    assert result.calc["_iwr_applies"] is True
    assert result.calc["_iwr_free_withdrawal_amount"] == pytest.approx(3_000.0)
    assert result.eod["SurrenderCharge"] == pytest.approx(0.0)
    assert result.eod["MVA"] == pytest.approx(0.0)


def test_iwr_free_amount_uses_rmd_for_tax_qualified_policy():
    val_state = {
        "ValuationDate": pd.Timestamp("2027-06-15"),
        "AccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 0.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "IWR",
        "AccumulatedInterestCurrentYear": 1_000.0,
        "RMD_Qualified": "Y",
        "RMD": 4_000.0,
    }
    event_input = {"Gross WD": 3_500.0, "Valuation Date": "2027-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_year1_year2(),
        rates_df=_mva_rates_for_event2_with_year2(),
    )

    assert result.calc["_iwr_free_amount_a"] == pytest.approx(1_000.0)
    assert result.calc["_iwr_free_amount_b"] == pytest.approx(4_000.0)
    assert result.calc["_iwr_free_withdrawal_amount"] == pytest.approx(4_000.0)
    assert result.eod["SurrenderCharge"] == pytest.approx(0.0)
    assert result.eod["MVA"] == pytest.approx(0.0)


def test_iwr_above_free_amount_applies_sc_and_mva_to_entire_withdrawal():
    gross_wd = 5_000.0
    val_state = {
        "ValuationDate": pd.Timestamp("2027-06-15"),
        "AccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 0.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "IWR",
        "AccumulatedInterestCurrentYear": 3_000.0,
        "RMD_Qualified": "N",
        "RMD": 0.0,
    }
    event_input = {"Gross WD": gross_wd, "Valuation Date": "2027-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_year1_year2(),
        rates_df=_mva_rates_for_event2_with_year2(),
    )

    remaining_months = month_diff("2027-06-15", "2031-01-15")
    expected_mva = compute_mva(gross_wd, 0.05, 0.043, remaining_months)

    assert result.calc["_mva_excess_amount"] == pytest.approx(gross_wd)
    assert result.eod["SurrenderChargeRate"] == pytest.approx(0.07)
    assert result.eod["SurrenderCharge"] == pytest.approx(gross_wd * 0.07)
    assert result.eod["MVA"] == pytest.approx(expected_mva)


def test_iwr_in_policy_year_1_emits_warning_and_does_not_apply_rider_waiver():
    val_state = {
        "ValuationDate": pd.Timestamp("2026-06-15"),
        "AccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 10_000.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "IWR",
        "AccumulatedInterestCurrentYear": 8_000.0,
        "RMD_Qualified": "N",
        "RMD": 0.0,
    }
    event_input = {"Gross WD": 5_000.0, "Valuation Date": "2026-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_year1_year2(),
        rates_df=_mva_rates_for_event2_with_year2(),
    )

    warnings = dict(result.validation.warnings())
    assert result.calc["_iwr_applies"] is False
    assert "InterestWithdrawalRider" in warnings
    assert result.eod["SurrenderCharge"] == pytest.approx((100_000.0 - 5_000.0) * 0.08)


def test_lbr_first_withdrawal_within_limit_waives_sc_and_mva():
    val_state = {
        "ValuationDate": pd.Timestamp("2026-06-15"),
        "AccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 0.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "LBR",
        "Withdrawal_Count": 1,
        "PriorYear_RiderWithdrawalUsed": "F",
        "RMD_Qualified": "N",
        "Tax_Qualified": "N",
        "RMD": 0.0,
    }
    event_input = {"Gross WD": 9_000.0, "Valuation Date": "2026-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_standard(),
        rates_df=_mva_rates_for_event2(),
        product_tables=_product_tables_for_riders(),
    )

    assert result.calc["_wd_rider_for_waiver"] == "LBR"
    assert result.calc["_wd_rider_applies"] is True
    assert result.calc["_wd_rider_limit_amount"] == pytest.approx(10_000.0)
    assert result.eod["SurrenderCharge"] == pytest.approx(0.0)
    assert result.eod["MVA"] == pytest.approx(0.0)


def test_lbr_base_amount_uses_single_premium_in_first_contract_year():
    val_state = {
        "ValuationDate": pd.Timestamp("2026-06-15"),
        "AccountValue": 80_000.0,
        "SinglePremium": 100_000.0,
        "PrecedingContractAnniversaryAccountValue": 80_000.0,
        "PenaltyFreeWithdrawalBalance": 0.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "LBR",
        "Withdrawal_Count": 1,
        "PriorYear_RiderWithdrawalUsed": "F",
        "RMD_Qualified": "N",
        "Tax_Qualified": "N",
        "RMD": 0.0,
    }
    event_input = {"Gross WD": 9_000.0, "Valuation Date": "2026-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_standard(),
        rates_df=_mva_rates_for_event2(),
        product_tables=_product_tables_for_riders(),
    )

    assert result.calc["_wd_policy_year"] == 1
    assert result.calc["_wd_penalty_free_base_source"] == "SinglePremium"
    assert result.calc["_wd_penalty_free_base_amount"] == pytest.approx(100_000.0)
    assert result.calc["_wd_rider_limit_amount"] == pytest.approx(10_000.0)
    assert result.eod["SurrenderCharge"] == pytest.approx(0.0)
    assert result.eod["MVA"] == pytest.approx(0.0)


def test_lbr_base_amount_uses_preceding_anniversary_value_after_first_contract_year():
    gross_wd = 11_000.0
    val_state = {
        "ValuationDate": pd.Timestamp("2027-06-15"),
        "AccountValue": 120_000.0,
        "SinglePremium": 100_000.0,
        "PrecedingContractAnniversaryAccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 0.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "LBR",
        "Withdrawal_Count": 1,
        "PriorYear_RiderWithdrawalUsed": "F",
        "RMD_Qualified": "N",
        "Tax_Qualified": "N",
        "RMD": 0.0,
    }
    event_input = {"Gross WD": gross_wd, "Valuation Date": "2027-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_year1_year2(),
        rates_df=_mva_rates_for_event2_with_year2(),
        product_tables=_product_tables_for_riders(),
    )

    expected_mva = compute_mva(gross_wd, 0.05, 0.043, month_diff("2027-06-15", "2031-01-15"))
    assert result.calc["_wd_policy_year"] == 2
    assert result.calc["_wd_penalty_free_base_source"] == "PrecedingContractAnniversaryAccountValue"
    assert result.calc["_wd_penalty_free_base_amount"] == pytest.approx(100_000.0)
    assert result.calc["_wd_rider_limit_amount"] == pytest.approx(10_000.0)
    assert result.eod["SurrenderChargeRate"] == pytest.approx(0.07)
    assert result.eod["SurrenderCharge"] == pytest.approx(gross_wd * 0.07)
    assert result.eod["MVA"] == pytest.approx(expected_mva)


def test_lbr_above_limit_applies_sc_and_mva_to_entire_withdrawal():
    gross_wd = 15_000.0
    val_state = {
        "ValuationDate": pd.Timestamp("2026-06-15"),
        "AccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 0.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "LBR",
        "Withdrawal_Count": 1,
        "PriorYear_RiderWithdrawalUsed": "F",
        "RMD_Qualified": "N",
        "Tax_Qualified": "N",
        "RMD": 0.0,
    }
    event_input = {"Gross WD": gross_wd, "Valuation Date": "2026-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_standard(),
        rates_df=_mva_rates_for_event2(),
        product_tables=_product_tables_for_riders(),
    )

    expected_mva = compute_mva(gross_wd, 0.05, 0.043, month_diff("2026-06-15", "2031-01-15"))
    assert result.calc["_wd_rider_applies"] is True
    assert result.calc["_mva_excess_amount"] == pytest.approx(gross_wd)
    assert result.eod["SurrenderCharge"] == pytest.approx(gross_wd * 0.08)
    assert result.eod["MVA"] == pytest.approx(expected_mva)


def test_elbr_uses_enhanced_limit_when_prior_year_not_used():
    val_state = {
        "ValuationDate": pd.Timestamp("2026-06-15"),
        "AccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 0.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "ELBR",
        "Withdrawal_Count": 1,
        "PriorYear_RiderWithdrawalUsed": "F",
        "RMD_Qualified": "N",
        "Tax_Qualified": "N",
        "RMD": 0.0,
    }
    event_input = {"Gross WD": 15_000.0, "Valuation Date": "2026-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_standard(),
        rates_df=_mva_rates_for_event2(),
        product_tables=_product_tables_for_riders(),
    )

    assert result.calc["_wd_rider_for_waiver"] == "ELBR"
    assert result.calc["_elbr_limit_mode"] == "enhanced"
    assert result.calc["_wd_rider_limit_amount"] == pytest.approx(20_000.0)
    assert result.eod["SurrenderCharge"] == pytest.approx(0.0)
    assert result.eod["MVA"] == pytest.approx(0.0)


def test_elbr_uses_regular_limit_when_prior_year_used():
    gross_wd = 15_000.0
    val_state = {
        "ValuationDate": pd.Timestamp("2026-06-15"),
        "AccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 0.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "ELBR",
        "Withdrawal_Count": 1,
        "PriorYear_RiderWithdrawalUsed": "T",
        "RMD_Qualified": "N",
        "Tax_Qualified": "N",
        "RMD": 0.0,
    }
    event_input = {"Gross WD": gross_wd, "Valuation Date": "2026-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_standard(),
        rates_df=_mva_rates_for_event2(),
        product_tables=_product_tables_for_riders(),
    )

    expected_mva = compute_mva(gross_wd, 0.05, 0.043, month_diff("2026-06-15", "2031-01-15"))
    assert result.calc["_elbr_limit_mode"] == "regular"
    assert result.calc["_wd_rider_limit_amount"] == pytest.approx(10_000.0)
    assert result.calc["_mva_excess_amount"] == pytest.approx(gross_wd)
    assert result.eod["SurrenderCharge"] == pytest.approx(gross_wd * 0.08)
    assert result.eod["MVA"] == pytest.approx(expected_mva)


def test_wd_rider_conflict_records_error_and_disables_waiver():
    val_state = {
        "ValuationDate": pd.Timestamp("2027-06-15"),
        "AccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 5_000.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "IWR, LBR",
        "AccumulatedInterestCurrentYear": 8_000.0,
        "RMD_Qualified": "N",
        "Tax_Qualified": "N",
        "RMD": 0.0,
        "Withdrawal_Count": 1,
        "PriorYear_RiderWithdrawalUsed": "F",
    }
    event_input = {"Gross WD": 7_000.0, "Valuation Date": "2027-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_year1_year2(),
        rates_df=_mva_rates_for_event2_with_year2(),
        product_tables=_product_tables_for_riders(),
    )

    assert "SelectedRiders" in dict(result.validation.errors())
    assert result.calc["_wd_rider_conflict"] is True
    assert result.calc["_wd_rider_applies"] is False
    assert result.calc["_mva_excess_amount"] == pytest.approx(2_000.0)


def test_lbr_ignores_input_penalty_free_withdrawal_amount_and_uses_calculated_amount():
    gross_wd = 11_000.0
    val_state = {
        "ValuationDate": pd.Timestamp("2026-06-15"),
        "AccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 0.0,
        "PenaltyFreeWithdrawalAmount": 12_000.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "LBR",
        "Withdrawal_Count": 1,
        "PriorYear_RiderWithdrawalUsed": "F",
        "RMD_Qualified": "N",
        "Tax_Qualified": "N",
        "RMD": 0.0,
    }
    event_input = {"Gross WD": gross_wd, "Valuation Date": "2026-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_standard(),
        rates_df=_mva_rates_for_event2(),
        product_tables=_product_tables_for_riders(),
    )

    expected_mva = compute_mva(gross_wd, 0.05, 0.043, month_diff("2026-06-15", "2031-01-15"))
    assert result.calc["_wd_rider_limit_amount"] == pytest.approx(10_000.0)
    assert result.eod["SurrenderCharge"] == pytest.approx(gross_wd * 0.08)
    assert result.eod["MVA"] == pytest.approx(expected_mva)


def test_elbr_ignores_input_enhanced_amount_and_uses_calculated_amount():
    gross_wd = 21_000.0
    val_state = {
        "ValuationDate": pd.Timestamp("2026-06-15"),
        "AccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 0.0,
        "EnhancedPenaltyFreeWithdrawalAmount": 30_000.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "ELBR",
        "Withdrawal_Count": 1,
        "PriorYear_RiderWithdrawalUsed": "F",
        "RMD_Qualified": "N",
        "Tax_Qualified": "N",
        "RMD": 0.0,
    }
    event_input = {"Gross WD": gross_wd, "Valuation Date": "2026-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_standard(),
        rates_df=_mva_rates_for_event2(),
        product_tables=_product_tables_for_riders(),
    )

    expected_mva = compute_mva(gross_wd, 0.05, 0.043, month_diff("2026-06-15", "2031-01-15"))
    assert result.calc["_elbr_limit_mode"] == "enhanced"
    assert result.calc["_wd_rider_limit_amount"] == pytest.approx(20_000.0)
    assert result.eod["SurrenderCharge"] == pytest.approx(gross_wd * 0.08)
    assert result.eod["MVA"] == pytest.approx(expected_mva)


def test_event2_recalculates_penalty_free_amount_fields_ignoring_input_values():
    val_state = {
        "ValuationDate": pd.Timestamp("2027-06-15"),
        "AccountValue": 120_000.0,
        "SinglePremium": 100_000.0,
        "PrecedingContractAnniversaryAccountValue": 100_000.0,
        "PenaltyFreeWithdrawalAmount": 123.0,
        "EnhancedPenaltyFreeWithdrawalAmount": 456.0,
        "PenaltyFreeWithdrawalBalance": 0.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "LBR",
        "Withdrawal_Count": 1,
        "PriorYear_RiderWithdrawalUsed": "F",
        "RMD_Qualified": "N",
        "Tax_Qualified": "N",
        "RMD": 0.0,
    }
    event_input = {"Gross WD": 100.0, "Valuation Date": "2027-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_year1_year2(),
        rates_df=_mva_rates_for_event2_with_year2(),
        product_tables=_product_tables_for_riders(),
    )

    assert result.eod["PenaltyFreeWithdrawalAmount"] == pytest.approx(10_000.0)
    assert result.eod["EnhancedPenaltyFreeWithdrawalAmount"] == pytest.approx(20_000.0)


def test_withdrawal_count_contract_year_increments_on_withdrawal():
    val_state = {
        "ValuationDate": pd.Timestamp("2026-06-15"),
        "AccountValue": 100_000.0,
        "PenaltyFreeWithdrawalBalance": 10_000.0,
        "IssueDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodStartDate": pd.Timestamp("2026-01-15"),
        "GuaranteePeriodEndDate": pd.Timestamp("2031-01-15"),
        "MVAReferenceRateAtStart": 0.05,
        "_mva_column": "Y05",
        "SelectedRiders": "",
        "WithdrawalCount_ContractYear": 1,
        "RMD_Qualified": "N",
        "Tax_Qualified": "N",
        "RMD": 0.0,
    }
    event_input = {"Gross WD": 500.0, "Valuation Date": "2026-06-15"}

    result = process_withdrawal(
        val_state=val_state,
        event_input=event_input,
        sc_tbl=_sc_table_standard(),
        rates_df=_mva_rates_for_event2(),
        product_tables=_product_tables_for_riders(),
    )

    assert result.eod["WithdrawalCount_ContractYear"] == 2
    assert result.eod["Withdrawal_Count"] == 2
