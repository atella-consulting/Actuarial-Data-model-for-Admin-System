"""
events/event_2.py
-----------------
Event 2 — PartialWithdrawal.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from models import EventOutput
from utils import (
    to_ts,
    sfloat,
    nonempty,
    pick_first,
    merge_state,
)
from calculations import (
    snapshot,
    get_mva_rate,
    is_mva_waiver_window,
    compute_mva,
    compute_death_benefit_amount,
    parse_selected_riders,
    policy_year,
    month_diff,
    free_withdrawal_components,
)
from validation import validate_withdrawal


# ---------------------------------------------------------------------------
# Input extraction helper
# ---------------------------------------------------------------------------

def extract_event2_input(row: "pd.Series") -> Optional[Dict[str, Any]]:
    """
    Inspect the policy input row and return an event-input dict for
    Event 2 if a non-zero Gross WD is present.

    Event 2 valuation date is normally supplied by the orchestrator
    after it derives the next calendar day from Event 1's valuation date.
    If no explicit event date is present in *event_input*, the processor
    falls back to the incoming valuation state date.
    """
    gross_wd = pick_first(row, "Gross WD", "GrossWD")

    # No withdrawal at all
    if not nonempty(gross_wd):
        return None
    if sfloat(gross_wd, 0.0) == 0.0:
        return None

    return {
        "EventType": "PartialWithdrawal",
        "GrossWD":  gross_wd,
        "Net":       None,
        "Tax":       None,
    }


# ---------------------------------------------------------------------------
# Event processor
# ---------------------------------------------------------------------------

def process_withdrawal(
    val_state: Dict[str, Any],
    event_input: Dict[str, Any],
    sc_tbl: Optional[pd.DataFrame],
    rates_df: Optional[pd.DataFrame] = None,
) -> EventOutput:
    """
    Process Event 2 — PartialWithdrawal.

    Reads the event input, validates withdrawal amounts against the
    current account value and the applicable charge-free withdrawal
    limit, applies
    the withdrawal, and returns a fully structured :class:`EventOutput`.

    Raises
    ------
    ValueError
        If the gross withdrawal exceeds the account value (fatal error),
        or if reference rates are missing for an excess withdrawal.
    """
    # ------------------------------------------------------------------
    # 1. Parse event inputs
    # ------------------------------------------------------------------
    event_date_raw = event_input.get("Valuation Date")
    event_date     = (
        to_ts(event_date_raw)
        if nonempty(event_date_raw)
        else to_ts(val_state["ValuationDate"])
    )

    gross_wd = sfloat(event_input.get("GrossWD") or event_input.get("Gross WD"), 0.0)
    net      = sfloat(event_input.get("Net"),  None) if nonempty(event_input.get("Net"))  else None
    tax      = sfloat(event_input.get("Tax"),  None) if nonempty(event_input.get("Tax"))  else None

    # ------------------------------------------------------------------
    # 2. Pre-withdrawal balances and rider context
    # ------------------------------------------------------------------
    pre_av = sfloat(val_state.get("AccountValue"))
    pfwb   = sfloat(val_state.get("PenaltyFreeWithdrawalBalance"), 0.0)
    issue_dt = to_ts(val_state.get("IssueDate"))
    gp_start = to_ts(val_state.get("GuaranteePeriodStartDate"))
    gp_end   = to_ts(val_state.get("GuaranteePeriodEndDate"))

    rider_set = set(parse_selected_riders(val_state.get("SelectedRiders")))
    has_interest_withdrawal_rider = bool(rider_set.intersection({"IWR", "EIWR"}))
    wd_policy_year = policy_year(issue_dt, event_date)
    rider_applies = has_interest_withdrawal_rider and wd_policy_year >= 2

    # Interest Withdrawal Rider free amount:
    #   A = AccumulatedInterestCurrentYear
    #   B = RMD (only when tax-qualified)
    #   Free = max(A, B)
    free_amount_parts = free_withdrawal_components(
        accumulated_interest_current_year=val_state.get("AccumulatedInterestCurrentYear"),
        rmd=val_state.get("RMD"),
        tax_qualified=val_state.get("Tax_Qualified"),
        rmd_qualified=val_state.get("RMD_Qualified"),
    )
    accum_interest = free_amount_parts["a"]
    rmd_component = free_amount_parts["b"]
    tax_qualified = free_amount_parts["tax_qualified"]
    free_withdrawal_amount = free_amount_parts["free_withdrawal_amount"]

    charge_free_limit = free_withdrawal_amount if rider_applies else pfwb
    charge_free_limit_label = "FreeWithdrawalAmount" if rider_applies else "PenaltyFreeWithdrawalBalance"

    # ------------------------------------------------------------------
    # 3. MVA rate look-ups
    #
    # A = rate at start of current guarantee period (stored on the policy
    #     at issue time via event_1 and carried forward in STATIC_CARRY)
    # B = rate on the day *preceding* the valuation date
    # ------------------------------------------------------------------
    rate_at_start: Optional[float] = sfloat(
        val_state.get("MVAReferenceRateAtStart"), None
    ) or None

    mva_column: Optional[str] = val_state.get("_mva_column")

    # Look up B
    day_before_event = event_date - pd.Timedelta(days=1) if not pd.isna(event_date) else None
    rate_current: Optional[float] = get_mva_rate(rates_df, day_before_event, column=mva_column)

    # ------------------------------------------------------------------
    # 4. Validation (includes MVA rate checks when charge-bearing amount exists)
    # ------------------------------------------------------------------
    result = validate_withdrawal(
        gross_wd=gross_wd,
        pre_av=pre_av,
        pfwb=pfwb,
        event_date_provided=nonempty(event_date_raw),
        rate_at_start=rate_at_start,
        rate_current=rate_current,
        charge_free_limit=charge_free_limit,
        charge_free_limit_label=charge_free_limit_label,
    )
    if has_interest_withdrawal_rider and wd_policy_year < 2:
        result.add_warning(
            "InterestWithdrawalRider",
            "Interest Withdrawal Rider starts in Policy Year 2; "
            f"withdrawal is in Policy Year {wd_policy_year}, so rider waiver was not applied.",
        )

    if result.has_errors():
        raise ValueError(
            f"[PartialWithdrawal] fatal validation errors:\n{result.error_summary()}"
        )

    # ------------------------------------------------------------------
    # 5. Compute MVA
    #
    # Default rule:
    #   MVA applies only to withdrawal above the charge-free limit.
    #
    # Interest Withdrawal Rider override (when rider applies):
    #   - If GrossWD <= FreeWithdrawalAmount: MVA is waived.
    #   - If GrossWD >  FreeWithdrawalAmount: MVA applies to the ENTIRE
    #     withdrawal amount, not just the excess.
    #
    # The MVA is waived entirely during the 30-day window at the start of
    # the guarantee period.
    # ------------------------------------------------------------------
    if rider_applies:
        iwr_waived_charges = gross_wd <= free_withdrawal_amount
        mva_charge_amount = 0.0 if iwr_waived_charges else gross_wd
    else:
        iwr_waived_charges = False
        mva_charge_amount = max(0.0, gross_wd - pfwb)

    # Whole months remaining in the guarantee period (used as 't').
    remaining_months = month_diff(event_date, gp_end)

    # Check waiver window first.
    in_waiver_window = is_mva_waiver_window(event_date, gp_start)

    if (
        in_waiver_window
        or mva_charge_amount <= 0.0
        or rate_at_start is None
        or rate_current is None
    ):
        mva = 0.0
        mva_waived = in_waiver_window or iwr_waived_charges
    else:
        mva = compute_mva(mva_charge_amount, rate_at_start, rate_current, remaining_months)
        mva_waived = False

    # ------------------------------------------------------------------
    # 6. Apply withdrawal
    # ------------------------------------------------------------------
    post_av   = pre_av - gross_wd
    post_pfwb = max(0.0, pfwb - gross_wd)

    # ------------------------------------------------------------------
    # 7. Assemble data / calc dicts
    # ------------------------------------------------------------------
    data: Dict[str, Any] = {
        "ValuationDate": event_date,
        "Event":         "PartialWithdrawal",
        "GrossWD":       gross_wd,
        "Net":           net,
        "Tax":           tax,
    }

    # Build the standard snapshot (SC, CSV, remaining months).
    snap = snapshot(
        event_date,
        post_av,
        issue_dt,
        gp_end,
        sc_tbl,
    )

    # Interest Withdrawal Rider surrender-charge rule:
    #   - within free amount: surrender charge is waived
    #   - above free amount : surrender charge applies to entire withdrawal
    if rider_applies:
        if gross_wd <= free_withdrawal_amount:
            snap["SurrenderCharge"] = 0.0
        else:
            snap["SurrenderCharge"] = gross_wd * snap["SurrenderChargeRate"]

    snap["MVA"] = mva  # override the placeholder 0.0 with the actual MVA
    # Recalculate CashSurrenderValue to include the MVA adjustment.
    snap["CashSurrenderValue"] = (
        post_av + mva - snap["SurrenderCharge"]
    )
    snap["Death_Benefit_Amount"] = compute_death_benefit_amount(
        selected_riders=val_state.get("SelectedRiders"),
        accumulation_value=post_av,
        cash_surrender_value=snap["CashSurrenderValue"],
    )

    calc: Dict[str, Any] = {
        "AccountValue":              post_av,
        "PenaltyFreeWithdrawalBalance": post_pfwb,
        "Free_Withdrawal_Amount":    free_withdrawal_amount,
        # Debug / audit fields recorded in calc block
        "_mva_excess_amount":        mva_charge_amount,
        "_mva_rate_at_start":        rate_at_start,
        "_mva_rate_current":         rate_current,
        "_mva_remaining_months":     remaining_months,
        "_mva_waived":               mva_waived,
        "_iwr_selected":             has_interest_withdrawal_rider,
        "_iwr_applies":              rider_applies,
        "_iwr_policy_year":          wd_policy_year,
        "_iwr_tax_qualified":        tax_qualified,
        "_iwr_free_amount_a":        accum_interest,
        "_iwr_free_amount_b":        rmd_component,
        "_iwr_free_withdrawal_amount": free_withdrawal_amount,
        "_iwr_waived_charges":       iwr_waived_charges,
        **snap,
    }

    # ------------------------------------------------------------------
    # 8. Build end-of-day state
    # ------------------------------------------------------------------
    eod = merge_state(data, calc, base=val_state)

    return EventOutput(
        event_type="PartialWithdrawal",
        data=data,
        calc=calc,
        validation=result,
        eod=eod,
    )
