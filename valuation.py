"""
valuation.py
------------
Daily roll-forward (EOD valuation) for the MYGA/FIA actuarial engine.

The single public function :func:`roll_forward` advances a policy's
end-of-day state by one or more calendar days, applying interest accrual
and resetting ``AccumulatedInterestCurrentYear`` on policy anniversaries.

It does **not** apply any events (withdrawals, surrenders, etc.).
Events are handled separately in the ``events/`` package.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from config import STATIC_CARRY, PLAN_YEARS
from calculations import (
    snapshot,
    compute_death_benefit_amount,
    free_withdrawal_components,
)
from utils import to_ts, sfloat, safe_replace_year, as_code


def roll_forward(
    prior_eod: Dict[str, Any],
    sc_tbl: Optional[pd.DataFrame],
    target_date: Any = None,
) -> Dict[str, Any]:
    """
    Advance the policy state from *prior_eod* to *target_date*.

    If *target_date* is ``None`` or unparseable, the function defaults to
    the calendar day immediately following the prior valuation date.

    Steps
    -----
    1. Determine the number of days elapsed (``day_count``).
    2. Grow ``AccountValue`` using the crediting rate on a 365-day basis.
    3. Subtract any daily contract charge (currently zero).
    4. On a policy anniversary, reset ``AccumulatedInterestCurrentYear``
       to the interest earned in the current period only.
    5. Carry forward all static fields from *prior_eod*.
    6. Build the standard derived snapshot (surrender charge, CSV, etc.).

    Parameters
    ----------
    prior_eod : dict
        The full end-of-day state produced by the previous event or
        the previous ``roll_forward`` call.
    sc_tbl : pd.DataFrame or None
        Surrender charge lookup table (``["Year", "ChargeRate"]`` columns).
    target_date : date-like, optional
        The valuation date to roll forward to.  Defaults to
        ``prior_eod["ValuationDate"] + 1 day``.

    Returns
    -------
    dict
        New end-of-day state dictionary with ``Event`` set to
        ``"Valuation"``.  This dict is suitable as input to another
        ``roll_forward`` call or to an event processor.
    """
    prior_date = to_ts(prior_eod["ValuationDate"])
    new_date = to_ts(target_date) if target_date is not None else pd.NaT

    # Default to the next calendar day if no target date was supplied.
    if pd.isna(new_date):
        new_date = prior_date + pd.Timedelta(days=1)

    day_count = max((new_date - prior_date).days, 0)

    issue_dt = to_ts(prior_eod["IssueDate"])
    gp_end   = to_ts(prior_eod["GuaranteePeriodEndDate"])
    ccr      = sfloat(prior_eod.get("CurrentCreditRate"))
    prior_av = sfloat(prior_eod.get("AccountValue"))
    mva_column   = prior_eod.get("_mva_column")         # resolved rate-file tenor column

    effective_date = to_ts(prior_eod.get("EffectiveDate"))
    if pd.isna(effective_date):
        effective_date = issue_dt

    term_period = prior_eod.get("Term_Period")
    if term_period in (None, "", float("nan")):
        term_period = PLAN_YEARS.get(as_code(prior_eod.get("ProductType")))

    nfr = sfloat(prior_eod.get("NonforfeitureRate"))
    prior_gmav = sfloat(prior_eod.get("GuaranteedMinimumAV"), prior_av)
    mgsv_contract_charge = sfloat(prior_eod.get("_mgsv_cc"))

    # ------------------------------------------------------------------
    # 1. Grow account value using compound interest on a 365-day basis.
    # ------------------------------------------------------------------
    growth = (1 + ccr) ** (day_count / 365) if day_count > 0 else 1.0
    av_before_charge = prior_av * growth

    # ------------------------------------------------------------------
    # 2A. Grow guaranteed minimum account value using the
    #     nonforfeiture rate on a 365-day basis.
    # ------------------------------------------------------------------
    gmav_growth = (1 + nfr) ** (day_count / 365) if day_count > 0 else 1.0
    new_gmav = prior_gmav * gmav_growth

    # ------------------------------------------------------------------
    # 3. Handle AccumulatedInterestCurrentYear.
    #    On a policy anniversary the accumulator resets to the interest
    #    earned *since* that anniversary.  On any other day, add the
    #    period interest to the running total.
    # ------------------------------------------------------------------
    period_interest = av_before_charge - prior_av
    anniversary = safe_replace_year(issue_dt, new_date.year)

    if not pd.isna(anniversary) and new_date.date() == anniversary.date():
        acc_int = period_interest
        new_gmav -= mgsv_contract_charge
    else:
        acc_int = sfloat(prior_eod.get("AccumulatedInterestCurrentYear"), 0.0) + period_interest

    if pd.isna(anniversary):
        anniversary_next = pd.NaT
    elif new_date.date() < anniversary.date():
        anniversary_next = anniversary
    else:
        anniversary_next = safe_replace_year(issue_dt, new_date.year + 1)

    # ------------------------------------------------------------------
    # 4. Build the new state dict.
    #    Start by carrying forward all static fields, then overwrite the
    #    fields that change on every valuation.
    # ------------------------------------------------------------------
    valuation_state: Dict[str, Any] = {
        field: prior_eod.get(field) for field in STATIC_CARRY
    }
    snap = snapshot(new_date, av_before_charge, issue_dt, gp_end, sc_tbl)
    death_benefit_amount = compute_death_benefit_amount(
        selected_riders=prior_eod.get("SelectedRiders"),
        accumulation_value=av_before_charge,
        cash_surrender_value=snap.get("CashSurrenderValue"),
    )
    free_withdrawal_amount = free_withdrawal_components(
        accumulated_interest_current_year=acc_int,
        rmd=prior_eod.get("RMD"),
        tax_qualified=prior_eod.get("Tax_Qualified"),
        rmd_qualified=prior_eod.get("RMD_Qualified"),
    )["free_withdrawal_amount"]

    valuation_state.update(
        {
            "ValuationDate": new_date,
            "Event": "Valuation",
            "EffectiveDate": effective_date,
            "Term_Period": term_period,
            "AnniversaryDateNext": anniversary_next,
            "AccountValue": av_before_charge,
            "GuaranteedMinimumAV": new_gmav,
            "DailyInterest": period_interest,
            "AccumulatedInterestCurrentYear": acc_int,
            "Free_Withdrawal_Amount": free_withdrawal_amount,
            # Derived snapshot fields
            **snap,
            "Death_Benefit_Amount": death_benefit_amount,
            # Clear transaction fields — they do not carry forward
            "GrossWD": None,
            "Net":     None,
            "Tax":     None,
            # Internal helpers — preserve for future rolls
            "_mgsv_cc":    mgsv_contract_charge,
            "_mva_column": mva_column,
        }
    )
    return valuation_state
