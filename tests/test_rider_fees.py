"""
tests/test_rider_fees.py
------------------------
Focused tests for SelectedRiders fee mapping and conflict warnings.
"""

from __future__ import annotations

import pandas as pd
import pytest

from calculations import parse_selected_riders, rider_credit_rate_adjustment
from events.event_1 import process_initialization


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
    assert "ELBR cannot be selected together with LBR" in warnings["SelectedRiders"]


def test_process_initialization_warns_when_eiwr_and_iwr_selected_together():
    result = process_initialization(
        row=_minimal_policy_row("EIWR, IWR"),
        sc_tbl=pd.DataFrame(columns=["Year", "ChargeRate"]),
        product_tables=_product_tables_for_riders(),
        rates_df=None,
        rmd_table=None,
    )

    warnings = dict(result.validation.warnings())
    assert "SelectedRiders" in warnings
    assert "EIWR cannot be selected together with IWR" in warnings["SelectedRiders"]

    # EIWR is placeholder-only for now, so only IWR fee is applied.
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
