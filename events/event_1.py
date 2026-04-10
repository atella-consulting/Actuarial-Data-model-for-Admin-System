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
    calculate_rmd,
    calculate_issue_age,
    rider_credit_rate_adjustment,
)
from validation import validate_initialization


def process_initialization(
    row: "pd.Series",
    sc_tbl: Optional[pd.DataFrame],
    product_tables: pd.DataFrame,
    rates_df: Optional[pd.DataFrame] = None,
    rmd_table: Optional[pd.DataFrame] = None,
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
    secondary_annuitant = to_ts(pick_first(row, "Secondary_AnnuitantDOB"))
    owner_dob    = to_ts(pick_first(row, "OwnerDOB"))
    premium      = sfloat(pick_first(row, "SinglePremium"))
    product_type = as_code(pick_first(row, "ProductType"))
    plan_code    = as_code(pick_first(row, "PlanCode"))
    state_raw    = pick_first(row, "State")
    state = str(state_raw).strip().upper() if nonempty(state_raw) else None
    rmd_qualified = pick_first(row, "RMD_Qualified", "RMD_qualified")

    primary_sex = pick_first(row, "Primary_Sex")
    secondary_sex = pick_first(row, "Secondary_Sex")
    term_certain_raw = pick_first(row, "TermCertain")
    annuity_type_raw = pick_first(row, "AnnuityType")

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
    input_total_riders_rate = to_pct(pick_first(row, "TotalRidersRate"))

    rider_adjustment = rider_credit_rate_adjustment(
        product_tables=product_tables,
        product_type=product_type,
        valuation_date=lookup_date,
        selected_riders=selected_riders,
    )
    total_riders_rate = (
        input_total_riders_rate
        if input_total_riders_rate is not None
        else rider_adjustment["total_fee"]
    )
    current_credit_rate -= total_riders_rate

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
    prior_year_end_balance_raw = pick_first(
        row,
        "PriorYearEndAccountValue",
        "RMD_PriorYearEndBalance",
        "AccountValueAsOfPriorDec31",
    )

    primary_issue_age = calculate_issue_age(annuitant, issue_dt)
    secondary_issue_age = calculate_issue_age(secondary_annuitant, issue_dt)
    term_certain = int(sfloat(term_certain_raw, -1)) if nonempty(term_certain_raw) else None
    prior_year_end_balance = sfloat(prior_year_end_balance_raw, 0.0)


    try:
        rmd = calculate_rmd(
            owner_dob=owner_dob,
            valuation_date=val_date,
            prior_year_end_balance=prior_year_end_balance_raw,
            rmd_qualified=rmd_qualified,
            rmd_table=rmd_table,
        )
    except ValueError:
        rmd = None

    rmd_pct = None
    if rmd is not None and prior_year_end_balance > 0:
        rmd_pct = rmd / prior_year_end_balance

    # ------------------------------------------------------------------
    # 8. Validation
    # ------------------------------------------------------------------
    result: ValidationResult = validate_initialization(
        issue_dt,
        primary_issue_age,
        premium,
        acc_int,
        product_type,
        state,
        lookup_ccr=lookup_ccr,
        lookup_gmir=lookup_gmir,
        lookup_nonforf=lookup_nonforf,
        lookup_date=lookup_date,
        rmd_qualified=rmd_qualified,
        owner_dob=owner_dob,
        rmd_value=rmd,
        rmd_prior_year_end_balance=prior_year_end_balance_raw,
)
    if result.has_errors():
        raise ValueError(
            f"[PolicyIssue] fatal validation errors:\n{result.error_summary()}"
        )

    if rider_adjustment["conflicts"]:
        result.add_warning(
            "SelectedRiders",
            "; ".join(rider_adjustment["conflicts"]),
        )

    # ------------------------------------------------------------------
    # 9. Assemble data / calc dicts
    # ------------------------------------------------------------------
    data: Dict[str, Any] = {
        "ValuationDate":                   val_date,
        "Event":                           "PolicyIssue",
        "PolicyNumber":                    pick_first(row, "PolicyNumber"),
        "IssueDate":                       issue_dt,
        "ProductType":                     product_type,
        "PlanCode":                        plan_code,
        "Primary_IssueAge":                primary_issue_age,
        "Primary_Sex":                     primary_sex,
        "Secondary_IssueAge":              secondary_issue_age,
        "Secondary_Sex":                   secondary_sex,
        "TermCertain":                     term_certain,
        "AnnuityType":                     annuity_type_raw,
        "RMD_Qualified":                   rmd_qualified,
        "State":                           state,
        "SinglePremium":                   premium,
        "SelectedRiders":                  selected_riders,
        "TotalRidersRate":                 total_riders_rate,
        "AnnuitantDOB":                    annuitant,
        "OwnerDOB":                        owner_dob,
        "Secondary_AnnuitantDOB":          pick_first(row, "Secondary_AnnuitantDOB"),
        "Secondary_OwnerDOB":              pick_first(row, "Secondary_OwnerDOB"),
        "AccountValue":                    account_value,
        "PriorYearEndAccountValue":        pick_first(row, "PriorYearEndAccountValue"),
        "AccumulatedInterestCurrentYear":  acc_int,
        "PenaltyFreeWithdrawalBalance":    pfwb,
        "RMD":                             rmd,
        "RMD%":                            rmd_pct,
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
        "TotalRidersRate":               total_riders_rate,
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
