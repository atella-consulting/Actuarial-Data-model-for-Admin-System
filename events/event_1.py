"""
events/event_1.py
-----------------
Event 1 — PolicyIssue (contract initialization).

Public API
----------
  process_initialization(row, sc_tbl, product_tables) -> EventOutput

This module is responsible for:
  1. Reading all raw policy-input values from the Excel row.
  2. Deriving calculated fields (guarantee dates, maturity date, rates).
  3. Validating the inputs via ``validation.validate_initialization``.
  4. Building and returning the full EOD state as an :class:`EventOutput`.

No other module should duplicate this logic.  The orchestrator
(Actuarial_Data_Model.py) calls this function once per policy.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from config import (
    GMIR,
    NONFORFEITURE,
    MVA_REF_RATE,
    PREMIUM_TAX_RATE,
    PLAN_YEARS,
)
from models import EventOutput, ValidationResult
from utils import (
    to_ts,
    to_pct,
    sfloat,
    nonempty,
    as_code,
    pick_first,
    merge_state,
    add_years,
)
from calculations import snapshot, maturity_date_from_issue_and_annuitant
from validation import validate_initialization


def process_initialization(
    row: "pd.Series",
    sc_tbl: Optional[pd.DataFrame],
    product_tables: Dict[str, Any],
) -> EventOutput:
    """
    Process Event 1 — PolicyIssue.

    Reads the first policy row from the input Excel file, derives all
    calculated fields, validates the data, and builds the initial
    end-of-day state.

    Parameters
    ----------
    row :
        A single ``pd.Series`` from the ``PolicyData`` sheet.
    sc_tbl :
        Surrender charge lookup table (``["Year", "ChargeRate"]``).
    product_tables :
        Nested dict loaded from the ``ProductTables`` sheet, e.g.::

            {
              "CreditingRate":  {"5-year": "5.75%"},
              "ContractCharge": {"Annual": 0},
            }

    Returns
    -------
    EventOutput
        ``event_type`` is ``"PolicyIssue"``.
        ``eod`` is the full end-of-day state, ready for ``roll_forward``.

    Raises
    ------
    ValueError
        If any fatal validation error (``E:`` prefix) is found.
    """
    # ------------------------------------------------------------------
    # 1. Parse raw inputs
    # ------------------------------------------------------------------
    issue_dt     = to_ts(pick_first(row, "IssueDate"))
    val_date     = to_ts(pick_first(row, "Valuation Date", "ValuationDate"))
    annuitant    = to_ts(pick_first(row, "AnnuitantDOB"))
    owner_dob    = to_ts(pick_first(row, "OwnerDOB"))
    premium      = sfloat(pick_first(row, "SinglePremium"))
    product_type = as_code(pick_first(row, "ProductType"))
    plan_code    = as_code(pick_first(row, "PlanCode"))

    # ------------------------------------------------------------------
    # 2. Resolve the guarantee term (3 / 5 / 7 / 10 years)
    # ------------------------------------------------------------------
    plan_key = product_type if product_type in PLAN_YEARS else plan_code
    if plan_key not in PLAN_YEARS:
        plan_key = "5"          # safe default
    plan_years = PLAN_YEARS[plan_key]

    # ------------------------------------------------------------------
    # 3. Selected riders — prefer the combined field, otherwise join columns
    # ------------------------------------------------------------------
    selected_riders = pick_first(row, "SelectedRiders")
    if not nonempty(selected_riders):
        selected_riders = ", ".join(
            str(row[c]).strip()
            for c in ("Rider 1", "Rider 2", "Rider 3")
            if c in row.index and nonempty(row[c])
        )

    # ------------------------------------------------------------------
    # 4. Guarantee period dates
    # ------------------------------------------------------------------
    gp_start = to_ts(pick_first(row, "GuaranteePeriodStartDate"))
    if pd.isna(gp_start):
        gp_start = issue_dt

    gp_end = to_ts(pick_first(row, "GuaranteePeriodEndDate"))
    if pd.isna(gp_end):
        gp_end = add_years(gp_start, plan_years)

    # ------------------------------------------------------------------
    # 5. Maturity date
    # ------------------------------------------------------------------
    maturity_date = to_ts(pick_first(row, "MaturityDate"))
    if pd.isna(maturity_date):
        maturity_date = maturity_date_from_issue_and_annuitant(issue_dt, annuitant)

    # ------------------------------------------------------------------
    # 6. Rates — prefer input values, then lookups, then hard defaults
    # ------------------------------------------------------------------
    rate_key = f"{plan_key}-year"
    lookup_ccr = (
        to_pct(product_tables.get("CreditingRate", {}).get(rate_key, 0.0))
        if product_tables
        else 0.0
    )

    gmir = to_pct(pick_first(row, "GuaranteedMinimumInterestRate"))
    if gmir is None:
        gmir = GMIR

    nonforf = to_pct(pick_first(row, "NonforfeitureRate"))
    if nonforf is None:
        nonforf = NONFORFEITURE

    premium_tax = to_pct(pick_first(row, "PremiumTaxRate"))
    if premium_tax is None:
        premium_tax = PREMIUM_TAX_RATE

    current_credit_rate = to_pct(pick_first(row, "CurrentCreditRate"))
    if current_credit_rate is None:
        current_credit_rate = lookup_ccr or 0.0

    mva_ref = to_pct(pick_first(row, "MVAReferenceRateAtStart"))
    if mva_ref is None:
        mva_ref = MVA_REF_RATE

    # ------------------------------------------------------------------
    # 7. Balance fields
    # ------------------------------------------------------------------
    account_value = sfloat(pick_first(row, "AccountValue"), premium)
    acc_int       = sfloat(pick_first(row, "AccumulatedInterestCurrentYear"), 0.0)
    pfwb          = sfloat(pick_first(row, "PenaltyFreeWithdrawalBalance"), 0.0)

    # Annual contract charge — placeholder; extend via product_tables later
    annual_contract_charge = 0.0

    # ------------------------------------------------------------------
    # 8. Validation
    # ------------------------------------------------------------------
    issue_age_raw = pick_first(row, "IssueAge")
    issue_age = sfloat(issue_age_raw, None) if nonempty(issue_age_raw) else None

    result: ValidationResult = validate_initialization(issue_dt, issue_age, premium)
    if result.has_errors():
        raise ValueError(
            f"[PolicyIssue] fatal validation errors:\n{result.error_summary()}"
        )

    # ------------------------------------------------------------------
    # 9. Assemble data / calc dicts
    # ------------------------------------------------------------------
    data: Dict[str, Any] = {
        "ValuationDate":                   val_date,
        "Event":                           "PolicyIssue",
        "PolicyNumber":                    pick_first(row, "PolicyNumber"),
        "IssueDate":                       issue_dt,
        "ProductType":                     pick_first(row, "ProductType"),
        "PlanCode":                        pick_first(row, "PlanCode"),
        "IssueAge":                        pick_first(row, "IssueAge"),
        "State":                           pick_first(row, "State"),
        "SinglePremium":                   premium,
        "SelectedRiders":                  selected_riders,
        "AnnuitantDOB":                    annuitant,
        "OwnerDOB":                        owner_dob,
        "AccountValue":                    account_value,
        "AccumulatedInterestCurrentYear":  acc_int,
        "PenaltyFreeWithdrawalBalance":    pfwb,
    }

    calc: Dict[str, Any] = {
        "GuaranteedMinimumInterestRate": gmir,
        "NonforfeitureRate":             nonforf,
        "MaturityDate":                  maturity_date,
        "PremiumTaxRate":                premium_tax,
        "GuaranteePeriodStartDate":      gp_start,
        "GuaranteePeriodEndDate":        gp_end,
        "CurrentCreditRate":             current_credit_rate,
        "MVAReferenceRateAtStart":       mva_ref,
        "DailyInterest":                 0.0,
        **snapshot(val_date, account_value, issue_dt, gp_end, sc_tbl),
    }

    # ------------------------------------------------------------------
    # 10. Build end-of-day state
    # ------------------------------------------------------------------
    eod = merge_state(
        data,
        calc,
        extras={
            "GrossWD": None,
            "Net":     None,
            "Tax":     None,
            "_cc":     annual_contract_charge,  # internal field for roll_forward
        },
    )

    return EventOutput(
        event_type="PolicyIssue",
        data=data,
        calc=calc,
        validation=result,
        eod=eod,
    )
