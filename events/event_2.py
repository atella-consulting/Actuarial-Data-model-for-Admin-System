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
)
from validation import validate_withdrawal


# ---------------------------------------------------------------------------
# Input extraction helper
# ---------------------------------------------------------------------------

def extract_event2_input(row: "pd.Series") -> Optional[Dict[str, Any]]:
    """
    Inspect the policy input row and return an event-input dict for
    Event 2 if a non-zero Gross WD is present.
    """
    gross_wd = pick_first(row, "Gross WD")

    # No withdrawal at all
    if not nonempty(gross_wd):
        return None
    if sfloat(gross_wd, 0.0) == 0.0:
        return None

    event_date = pick_first(row, "Valuation Date.1")

    return {
        "EventType":    "PartialWithdrawal",
        "Valuation Date": event_date,
        "Gross WD":     gross_wd,
        "Net":          pick_first(row, "Net"),
        "Tax":          pick_first(row, "Tax"),
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
    current account value and penalty-free withdrawal balance, applies
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
    event_date_provided = nonempty(event_date_raw)

    gross_wd = sfloat(event_input.get("Gross WD"))
    net      = sfloat(event_input.get("Net"),  None) if nonempty(event_input.get("Net"))  else None
    tax      = sfloat(event_input.get("Tax"),  None) if nonempty(event_input.get("Tax"))  else None

    # ------------------------------------------------------------------
    # 2. Pre-withdrawal balances from the incoming valuation state
    # ------------------------------------------------------------------
    pre_av = sfloat(val_state.get("AccountValue"))
    pfwb   = sfloat(val_state.get("PenaltyFreeWithdrawalBalance"), 0.0)

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
    # 4. Validation (now includes MVA rate checks for excess withdrawals)
    # ------------------------------------------------------------------
    result = validate_withdrawal(
        gross_wd,
        pre_av,
        pfwb,
        event_date_provided,
        rate_at_start,
        rate_current,
    )
    if result.has_errors():
        raise ValueError(
            f"[PartialWithdrawal] fatal validation errors:\n{result.error_summary()}"
        )

    # ------------------------------------------------------------------
    # 5. Compute MVA
    #
    # MVA applies only to the portion of the withdrawal that exceeds the
    # penalty-free withdrawal balance (the "excess amount").
    # The MVA is waived entirely during the 30-day window at the start of
    # the guarantee period.
    # ------------------------------------------------------------------
    excess_amount = max(0.0, gross_wd - pfwb)

    gp_start = to_ts(val_state.get("GuaranteePeriodStartDate"))
    gp_end   = to_ts(val_state.get("GuaranteePeriodEndDate"))
    issue_dt = to_ts(val_state.get("IssueDate"))

    # Whole months remaining in the guarantee period (used as 't').
    from calculations import month_diff
    remaining_months = month_diff(event_date, gp_end)

    # Check waiver window first.
    in_waiver_window = is_mva_waiver_window(event_date, gp_start)

    if (
        in_waiver_window
        or excess_amount <= 0.0
        or rate_at_start is None
        or rate_current is None
    ):
        mva = 0.0
        mva_waived = in_waiver_window
    else:
        mva = compute_mva(excess_amount, rate_at_start, rate_current, remaining_months)
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
    snap["MVA"] = mva  # override the placeholder 0.0 with the actual MVA
    # Recalculate CashSurrenderValue to include the MVA adjustment.
    snap["CashSurrenderValue"] = (
        post_av + mva - snap["SurrenderCharge"]
    )

    calc: Dict[str, Any] = {
        "AccountValue":              post_av,
        "PenaltyFreeWithdrawalBalance": post_pfwb,
        # Debug / audit fields recorded in calc block
        "_mva_excess_amount":        excess_amount,
        "_mva_rate_at_start":        rate_at_start,
        "_mva_rate_current":         rate_current,
        "_mva_remaining_months":     remaining_months,
        "_mva_waived":               mva_waived,
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