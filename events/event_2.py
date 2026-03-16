"""
events/event_2.py
-----------------
Event 2 — PartialWithdrawal.

Public API
----------
  extract_event2_input(row)                        -> Optional[dict]
  process_withdrawal(val_state, event_input, sc_tbl) -> EventOutput

``extract_event2_input`` examines the raw policy row to see whether a
withdrawal event was included.  If it finds a non-zero Gross WD, it
returns a small input dict that ``process_withdrawal`` can consume.

``process_withdrawal`` applies the withdrawal to the pre-event valuation
state, validates the amounts, and returns a fully structured
:class:`~models.EventOutput`.
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
from calculations import snapshot
from validation import validate_withdrawal


# ---------------------------------------------------------------------------
# Input extraction helper
# ---------------------------------------------------------------------------

def extract_event2_input(row: "pd.Series") -> Optional[Dict[str, Any]]:
    """
    Inspect the policy input row and return an event-input dict for
    Event 2 if a non-zero Gross WD is present.

    Column-name resolution
    ----------------------
    The function tries several common variants for ``Gross WD`` and for
    the second valuation-date column (pandas may rename duplicate headers
    to ``"Valuation Date.1"``).

    Parameters
    ----------
    row :
        A single ``pd.Series`` from the ``PolicyData`` sheet.

    Returns
    -------
    dict or None
        ``None`` when no withdrawal is found.
        Otherwise a dict with keys:
        ``"EventType"``, ``"Valuation Date"``, ``"Gross WD"``,
        ``"Net"``, ``"Tax"``.
    """
    gross_wd = pick_first(row, "Gross WD", "GrossWD")

    # No withdrawal at all
    if not nonempty(gross_wd):
        return None
    if sfloat(gross_wd, 0.0) == 0.0:
        return None

    # Try several possible column names for the Event 2 valuation date.
    event_date = pick_first(
        row,
        "Valuation Date.1",
        "ValuationDate.1",
        "Event2 Valuation Date",
        "Event2ValuationDate",
    )

    # Fallback: scan for any pandas-renamed duplicate column
    if event_date is None:
        for col in row.index:
            col_str = str(col)
            if (
                col_str.startswith("Valuation Date.")
                or col_str.startswith("ValuationDate.")
            ) and nonempty(row[col]):
                event_date = row[col]
                break

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
) -> EventOutput:
    """
    Process Event 2 — PartialWithdrawal.

    Reads the event input, validates withdrawal amounts against the
    current account value and penalty-free withdrawal balance, applies
    the withdrawal, and returns a fully structured :class:`EventOutput`.

    Parameters
    ----------
    val_state : dict
        The pre-event valuation state produced by ``roll_forward()``.
        Must contain at minimum: ``ValuationDate``, ``AccountValue``,
        ``PenaltyFreeWithdrawalBalance``, ``IssueDate``,
        ``GuaranteePeriodEndDate``.
    event_input : dict
        The dict returned by :func:`extract_event2_input` (or any dict
        with the same keys).
    sc_tbl : pd.DataFrame or None
        Surrender charge lookup table.

    Returns
    -------
    EventOutput
        ``event_type`` is ``"PartialWithdrawal"``.
        ``eod`` is the full end-of-day state after the withdrawal.

    Raises
    ------
    ValueError
        If the gross withdrawal exceeds the account value (fatal error).
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
    # 3. Validation
    # ------------------------------------------------------------------
    result = validate_withdrawal(gross_wd, pre_av, pfwb, event_date_provided)
    if result.has_errors():
        raise ValueError(
            f"[PartialWithdrawal] fatal validation errors:\n{result.error_summary()}"
        )

    # ------------------------------------------------------------------
    # 4. Apply withdrawal
    # ------------------------------------------------------------------
    post_av   = pre_av - gross_wd
    post_pfwb = max(0.0, pfwb - gross_wd)

    # ------------------------------------------------------------------
    # 5. Assemble data / calc dicts
    # ------------------------------------------------------------------
    data: Dict[str, Any] = {
        "ValuationDate": event_date,
        "Event":         "PartialWithdrawal",
        "GrossWD":       gross_wd,
        "Net":           net,
        "Tax":           tax,
    }

    calc: Dict[str, Any] = {
        "AccountValue":              post_av,
        "PenaltyFreeWithdrawalBalance": post_pfwb,
        **snapshot(
            event_date,
            post_av,
            val_state.get("IssueDate"),
            val_state.get("GuaranteePeriodEndDate"),
            sc_tbl,
        ),
    }

    # ------------------------------------------------------------------
    # 6. Build end-of-day state
    # ------------------------------------------------------------------
    eod = merge_state(data, calc, base=val_state)

    return EventOutput(
        event_type="PartialWithdrawal",
        data=data,
        calc=calc,
        validation=result,
        eod=eod,
    )
