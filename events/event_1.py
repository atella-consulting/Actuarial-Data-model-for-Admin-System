"""
events/event_1.py
-----------------
Event 1 — PolicyIssue (contract initialization).

This module is responsible for:
  1. Reading all raw policy-input values from the Excel row.
  2. Deriving calculated fields (guarantee dates, maturity date, rates).
  3. Validating the inputs via ``validation.validate_initialization``.
  4. Building and returning the full EOD state as an :class:`EventOutput`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from config import (
    GMIR,
    NONFORFEITURE,
    PREMIUM_TAX_RATE,
    PLAN_YEARS,
    MGSV_CONTRACT_CHARGE
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
    safe_replace_year,
)
from calculations import (
    snapshot,
    maturity_date_from_issue_and_annuitant,
    resolve_mva_column,
    get_mva_rate,
    lookup_product_table_rate,
)
from validation import validate_initialization


def process_initialization(
    row: "pd.Series",
    sc_tbl: Optional[pd.DataFrame],
    product_tables: pd.DataFrame,
    rates_df: Optional[pd.DataFrame] = None,
) -> EventOutput:
    """
    Process Event 1 — PolicyIssue.

    Reads the first policy row from the input Excel file, derives all
    calculated fields, validates the data, and builds the initial
    end-of-day state.
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
    state_raw    = pick_first(row, "State")
    state = str(state_raw).strip().upper() if nonempty(state_raw) else None

    if pd.isna(val_date):
        val_date = issue_dt

    # ------------------------------------------------------------------
    # 2. Resolve the guarantee term (3 / 5 / 7 / 10 years)
    # ------------------------------------------------------------------
    if product_type not in PLAN_YEARS:
        raise ValueError(
            "ProductType must be one of: "
            "MYGA_03, MYGA_05, MYGA_07, MYGA_10. "
            f"Got: {product_type!r}"
        )

    plan_years = PLAN_YEARS[product_type]
    term_period = plan_years

    mva_column: str = resolve_mva_column(plan_years)

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

    effective_date = issue_dt
    anniversary_date_next = safe_replace_year(issue_dt, issue_dt.year + 1) if not pd.isna(issue_dt) else pd.NaT

    # ------------------------------------------------------------------
    # 5. Maturity date
    # ------------------------------------------------------------------
    maturity_date = to_ts(pick_first(row, "MaturityDate"))
    if pd.isna(maturity_date):
        maturity_date = maturity_date_from_issue_and_annuitant(issue_dt, annuitant)

    # ------------------------------------------------------------------
    # 6. Rates — prefer input values, then ProductTables lookups, then hard defaults
    # ------------------------------------------------------------------
    lookup_date = val_date if not pd.isna(val_date) else issue_dt

    lookup_ccr = lookup_product_table_rate(
        product_tables,
        "CreditingRate",
        product_type,
        lookup_date,
    )

    lookup_gmir = lookup_product_table_rate(
        product_tables,
        "GuaranteedMinimumInterestRate",
        product_type,
        lookup_date,
    )

    lookup_nonforf = lookup_product_table_rate(
        product_tables,
        "NonforfeitureRate",
        product_type,
        lookup_date,
    )

    gmir = lookup_gmir if lookup_gmir is not None else GMIR
    nonforf = lookup_nonforf if lookup_nonforf is not None else NONFORFEITURE
    premium_tax = PREMIUM_TAX_RATE
    current_credit_rate = lookup_ccr if lookup_ccr is not None else 0.0

    # print("DEBUG product_type:", product_type)
    # print("DEBUG lookup_date:", lookup_date)
    # print("DEBUG lookup_gmir:", lookup_gmir)
    # print("DEBUG lookup_nonforf:", lookup_nonforf)
    # print("DEBUG lookup_ccr:", lookup_ccr)

    mva_ref = None
    if rates_df is not None and not rates_df.empty:
        # This is "A" in the MVA formula — the reference rate at the beginning
        # of the current guarantee period.
        mva_ref = get_mva_rate(rates_df, gp_start, column=mva_column)
        # Raise an error if the start-date MVA reference rate cannot be found.
        if mva_ref is None:
            raise ValueError(
                f"Missing MVA reference rate at guarantee start date {gp_start} "
                f"for column {mva_column}."
            )

    # ------------------------------------------------------------------
    # 7. Balance fields
    # ------------------------------------------------------------------
    account_value = sfloat(pick_first(row, "AccountValue"), premium)
    guaranteed_minimum_av = sfloat(pick_first(row, "GuaranteedMinimumAV"), account_value)
    acc_int       = sfloat(pick_first(row, "AccumulatedInterestCurrentYear"), 0.0)
    pfwb          = sfloat(pick_first(row, "PenaltyFreeWithdrawalBalance"), 0.0)

    # ------------------------------------------------------------------
    # 8. Validation
    # ------------------------------------------------------------------
    issue_age_raw = pick_first(row, "Primary_IssueAge", "IssueAge")
    issue_age = sfloat(issue_age_raw, None) if nonempty(issue_age_raw) else None

    result: ValidationResult = validate_initialization(
        issue_dt,
        issue_age,
        premium,
        acc_int,
        product_type,
        state,
        lookup_ccr=lookup_ccr,
        lookup_gmir=lookup_gmir,
        lookup_nonforf=lookup_nonforf,
        lookup_date=lookup_date,
)
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
        "IssueAge":                        issue_age_raw,
        "State":                           state,
        "SinglePremium":                   premium,
        "SelectedRiders":                  selected_riders,
        "AnnuitantDOB":                    annuitant,
        "OwnerDOB":                        owner_dob,
        "AccountValue":                    account_value,
        "AccumulatedInterestCurrentYear":  acc_int,
        "PenaltyFreeWithdrawalBalance":    pfwb,
    }

    calc: Dict[str, Any] = {
        "EffectiveDate":                effective_date,
        "AnniversaryDateNext":         anniversary_date_next,
        "Term_Period":                 term_period,
        "GuaranteedMinimumInterestRate": gmir,
        "NonforfeitureRate":             nonforf,
        "GuaranteedMinimumAV":           guaranteed_minimum_av,
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
            "_mgsv_cc":     MGSV_CONTRACT_CHARGE,    # internal field for roll_forward
            "_mva_column":  mva_column,              # resolved tenor column for MVA lookups
        },
    )

    return EventOutput(
        event_type="PolicyIssue",
        data=data,
        calc=calc,
        validation=result,
        eod=eod,
    )