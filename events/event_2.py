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
    truthy_flag,
    lookup_free_withdrawal_percentage,
    benefit_withdrawal_components,
    is_within_guarantee_period,
    is_right_to_examine_period,
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
        "Net":       pick_first(row, "Net"),
        "Tax":       pick_first(row, "Tax"),
        "WithdrawalCount_ContractYear": pick_first(row, "WithdrawalCount_ContractYear", "Withdrawal_Count"),
        "Withdrawal_Count": pick_first(row, "WithdrawalCount_ContractYear", "Withdrawal_Count"),
        "PriorYear_RiderWithdrawalUsed": pick_first(row, "PriorYear_RiderWithdrawalUsed"),
    }


# ---------------------------------------------------------------------------
# Event processor
# ---------------------------------------------------------------------------

def process_withdrawal(
    val_state: Dict[str, Any],
    event_input: Dict[str, Any],
    sc_tbl: Optional[pd.DataFrame],
    rates_df: Optional[pd.DataFrame] = None,
    product_tables: Optional[pd.DataFrame] = None,
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
    event_date = (
        to_ts(event_date_raw)
        if nonempty(event_date_raw)
        else to_ts(val_state["ValuationDate"])
    )

    gross_wd = sfloat(event_input.get("GrossWD") or event_input.get("Gross WD"), 0.0)
    net = sfloat(event_input.get("Net"), None) if nonempty(event_input.get("Net")) else None
    tax = sfloat(event_input.get("Tax"), None) if nonempty(event_input.get("Tax")) else None

    wd_count_raw = event_input.get("WithdrawalCount_ContractYear")
    if not nonempty(wd_count_raw):
        wd_count_raw = event_input.get("Withdrawal_Count")
    if not nonempty(wd_count_raw):
        wd_count_raw = val_state.get("WithdrawalCount_ContractYear")
    if not nonempty(wd_count_raw):
        wd_count_raw = val_state.get("Withdrawal_Count")
    withdrawal_count_contract_year = int(max(0.0, sfloat(wd_count_raw, 0.0)))

    prior_year_rider_raw = event_input.get("PriorYear_RiderWithdrawalUsed")
    if not nonempty(prior_year_rider_raw):
        prior_year_rider_raw = val_state.get("PriorYear_RiderWithdrawalUsed")
    prior_year_rider_used = truthy_flag(prior_year_rider_raw)
    prior_year_rider_used_code = "T" if prior_year_rider_used else "F"

    # ------------------------------------------------------------------
    # 2. Pre-withdrawal balances and rider context
    # ------------------------------------------------------------------
    pre_av = sfloat(val_state.get("AccountValue"))
    pfwb = sfloat(val_state.get("PenaltyFreeWithdrawalBalance"), 0.0)
    issue_dt = to_ts(val_state.get("IssueDate"))
    gp_start = to_ts(val_state.get("GuaranteePeriodStartDate"))
    gp_end = to_ts(val_state.get("GuaranteePeriodEndDate"))

    rider_list = parse_selected_riders(val_state.get("SelectedRiders"))
    rider_set = set(rider_list)
    wd_rider_candidates = [r for r in rider_list if r in {"IWR", "LBR", "ELBR"}]
    wd_rider_conflict = len(wd_rider_candidates) > 1

    if wd_rider_conflict:
        wd_rider_for_waiver = None
    elif "ELBR" in rider_set:
        wd_rider_for_waiver = "ELBR"
    elif "LBR" in rider_set:
        wd_rider_for_waiver = "LBR"
    elif rider_set.intersection({"IWR", "EIWR"}):
        wd_rider_for_waiver = "IWR"
    else:
        wd_rider_for_waiver = None

    has_interest_withdrawal_rider = bool(rider_set.intersection({"IWR", "EIWR"}))
    wd_policy_year = policy_year(issue_dt, event_date)
    single_premium = sfloat(val_state.get("SinglePremium"), pre_av)
    preceding_contract_anniversary_av = sfloat(
        val_state.get("PrecedingContractAnniversaryAccountValue"),
        pre_av,
    )
    if wd_policy_year <= 1:
        penalty_free_base_amount = max(0.0, single_premium)
        penalty_free_base_source = "SinglePremium"
    else:
        penalty_free_base_amount = max(0.0, preceding_contract_anniversary_av)
        penalty_free_base_source = "PrecedingContractAnniversaryAccountValue"

    is_first_withdrawal_contract_year = withdrawal_count_contract_year <= 1
    within_guarantee_period = is_within_guarantee_period(event_date, gp_start, gp_end)
    in_right_to_examine_period = is_right_to_examine_period(event_date, issue_dt)

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

    # Liquidity Benefit Rider limits:
    #   PenaltyFreeBaseAmount:
    #     - contract year 1: SinglePremium
    #     - contract year 2+: pre-WD AccountValue at preceding Contract Anniversary
    #   A = PenaltyFreeBaseAmount * rider %
    #   B = RMD only when tax-qualified
    #   Limit = max(A, B)
    lbr_percentage_for_amount = None
    elbr_enhanced_percentage_for_amount = None
    if product_tables is not None:
        lbr_percentage_for_amount = lookup_free_withdrawal_percentage(
            product_tables=product_tables,
            rider_table_name="LiquidityBenefitWD",
            valuation_date=event_date,
        )
        elbr_enhanced_percentage_for_amount = lookup_free_withdrawal_percentage(
            product_tables=product_tables,
            rider_table_name="EnhLiquidityBenefitWD",
            valuation_date=event_date,
        )

    lbr_percentage = (
        lbr_percentage_for_amount
        if wd_rider_for_waiver in {"LBR", "ELBR"}
        else None
    )
    elbr_enhanced_percentage = (
        elbr_enhanced_percentage_for_amount
        if wd_rider_for_waiver == "ELBR"
        else None
    )
    calculated_penalty_free_withdrawal_amount = penalty_free_base_amount * max(
        0.0,
        sfloat(lbr_percentage_for_amount, 0.0),
    )
    calculated_enhanced_penalty_free_withdrawal_amount = penalty_free_base_amount * max(
        0.0,
        sfloat(elbr_enhanced_percentage_for_amount, 0.0),
    )

    lbr_parts = benefit_withdrawal_components(
        contract_value_base=penalty_free_base_amount,
        percentage=lbr_percentage,
        rmd=val_state.get("RMD"),
        tax_qualified=val_state.get("Tax_Qualified"),
        rmd_qualified=val_state.get("RMD_Qualified"),
    )
    lbr_limit_a = (
        lbr_parts["a"]
    )
    lbr_limit_b = lbr_parts["b"]
    lbr_limit = max(lbr_limit_a, lbr_limit_b)

    elbr_regular_parts = benefit_withdrawal_components(
        contract_value_base=penalty_free_base_amount,
        percentage=lbr_percentage,
        rmd=val_state.get("RMD"),
        tax_qualified=val_state.get("Tax_Qualified"),
        rmd_qualified=val_state.get("RMD_Qualified"),
    )
    elbr_enhanced_parts = benefit_withdrawal_components(
        contract_value_base=penalty_free_base_amount,
        percentage=elbr_enhanced_percentage,
        rmd=val_state.get("RMD"),
        tax_qualified=val_state.get("Tax_Qualified"),
        rmd_qualified=val_state.get("RMD_Qualified"),
    )
    elbr_regular_limit_a = lbr_limit_a
    elbr_regular_limit_b = elbr_regular_parts["b"]
    elbr_regular_limit = max(elbr_regular_limit_a, elbr_regular_limit_b)

    elbr_enhanced_limit_a = elbr_enhanced_parts["a"]
    elbr_enhanced_limit_b = elbr_enhanced_parts["b"]
    elbr_enhanced_limit = max(elbr_enhanced_limit_a, elbr_enhanced_limit_b)
    elbr_uses_enhanced_limit = not prior_year_rider_used
    elbr_limit = elbr_enhanced_limit if elbr_uses_enhanced_limit else elbr_regular_limit
    elbr_limit_mode = "enhanced" if elbr_uses_enhanced_limit else "regular"

    iwr_applies = wd_rider_for_waiver == "IWR" and wd_policy_year >= 2
    lbr_eligible = (
        wd_rider_for_waiver == "LBR"
        and within_guarantee_period
        and is_first_withdrawal_contract_year
        and not in_right_to_examine_period
    )
    elbr_eligible = (
        wd_rider_for_waiver == "ELBR"
        and within_guarantee_period
        and is_first_withdrawal_contract_year
        and not in_right_to_examine_period
    )
    lbr_applies = lbr_eligible and lbr_percentage is not None
    elbr_applies = (
        elbr_eligible
        and lbr_percentage is not None
        and elbr_enhanced_percentage is not None
    )

    wd_rider_applies = iwr_applies or lbr_applies or elbr_applies

    if iwr_applies:
        rider_limit_amount = free_withdrawal_amount
        charge_free_limit = free_withdrawal_amount
        charge_free_limit_label = "IWR_FreeWithdrawalAmount"
    elif lbr_applies:
        rider_limit_amount = lbr_limit
        charge_free_limit = lbr_limit
        charge_free_limit_label = "LBR_Limit"
    elif elbr_applies:
        rider_limit_amount = elbr_limit
        charge_free_limit = elbr_limit
        charge_free_limit_label = "ELBR_Limit"
    else:
        rider_limit_amount = None
        charge_free_limit = pfwb
        charge_free_limit_label = "PenaltyFreeWithdrawalBalance"

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

    non_blocking_error_fields = set()
    if wd_rider_conflict:
        result.add_error(
            "SelectedRiders",
            "Multiple WD riders selected from IWR, LBR, ELBR; rider waiver is not applied.",
        )
        non_blocking_error_fields.add("SelectedRiders")

    if wd_rider_for_waiver == "LBR" and lbr_eligible and lbr_percentage is None:
        result.add_error(
            "LBR_Percentage",
            "Missing LBR percentage source (FreeWD / LiquidityBenefitWD); LBR waiver is not applied.",
        )
        non_blocking_error_fields.add("LBR_Percentage")

    if wd_rider_for_waiver == "ELBR" and elbr_eligible:
        if lbr_percentage is None:
            result.add_error(
                "ELBR_RegularPercentage",
                "Missing ELBR regular percentage source (FreeWD / LiquidityBenefitWD); ELBR waiver is not applied.",
            )
            non_blocking_error_fields.add("ELBR_RegularPercentage")
        if elbr_enhanced_percentage is None:
            result.add_error(
                "ELBR_EnhancedPercentage",
                "Missing ELBR enhanced percentage source (FreeWD / EnhLiquidityBenefitWD); ELBR waiver is not applied.",
            )
            non_blocking_error_fields.add("ELBR_EnhancedPercentage")

    if has_interest_withdrawal_rider and wd_policy_year < 2 and wd_rider_for_waiver == "IWR":
        result.add_warning(
            "InterestWithdrawalRider",
            "Interest Withdrawal Rider starts in Policy Year 2; "
            f"withdrawal is in Policy Year {wd_policy_year}, so rider waiver was not applied.",
        )
    if wd_rider_for_waiver in {"LBR", "ELBR"} and not within_guarantee_period:
        result.add_warning(
            "LiquidityRider",
            "Liquidity rider waiver applies only during the guarantee period; waiver was not applied.",
        )
    if wd_rider_for_waiver in {"LBR", "ELBR"} and in_right_to_examine_period:
        result.add_warning(
            "LiquidityRider",
            "Liquidity rider waiver is not available during the 30-day Right-to-Examine period.",
        )
    if wd_rider_for_waiver in {"LBR", "ELBR"} and not is_first_withdrawal_contract_year:
        result.add_warning(
            "LiquidityRider",
            "Liquidity rider waiver applies only to the first withdrawal in the contract year.",
        )

    blocking_errors = [
        message
        for field, message in result.errors()
        if field not in non_blocking_error_fields
    ]
    if blocking_errors:
        raise ValueError(
            "[PartialWithdrawal] fatal validation errors:\n"
            + "\n".join(blocking_errors)
        )

    # ------------------------------------------------------------------
    # 5. Compute MVA
    #
    # Default rule:
    #   MVA applies only to withdrawal above the charge-free limit.
    #
    # Rider override (when rider applies):
    #   - If GrossWD <= RiderLimit: MVA is waived.
    #   - If GrossWD >  RiderLimit: MVA applies to the ENTIRE withdrawal.
    #
    # The MVA is waived entirely during the 30-day window at the start of
    # the guarantee period.
    # ------------------------------------------------------------------
    rider_waived_charges = wd_rider_applies and gross_wd <= sfloat(rider_limit_amount, 0.0)
    if wd_rider_applies:
        mva_charge_amount = 0.0 if rider_waived_charges else gross_wd
    else:
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
        mva_waived = in_waiver_window or rider_waived_charges
    else:
        mva = compute_mva(mva_charge_amount, rate_at_start, rate_current, remaining_months)
        mva_waived = False

    # ------------------------------------------------------------------
    # 6. Apply withdrawal
    # ------------------------------------------------------------------
    post_av = pre_av - gross_wd
    post_pfwb = max(0.0, pfwb - gross_wd)
    post_withdrawal_count = withdrawal_count_contract_year + (1 if gross_wd > 0.0 else 0)

    # ------------------------------------------------------------------
    # 7. Assemble data / calc dicts
    # ------------------------------------------------------------------
    data: Dict[str, Any] = {
        "ValuationDate": event_date,
        "Event": "PartialWithdrawal",
        "GrossWD": gross_wd,
        "Net": net,
        "Tax": tax,
        "WithdrawalCount_ContractYear": post_withdrawal_count,
        "Withdrawal_Count": post_withdrawal_count,
        "PriorYear_RiderWithdrawalUsed": prior_year_rider_used_code,
    }

    # Build the standard snapshot (SC, CSV, remaining months).
    snap = snapshot(
        event_date,
        post_av,
        issue_dt,
        gp_end,
        sc_tbl,
    )

    # Rider surrender-charge rule:
    #   - within rider limit: surrender charge is waived
    #   - above rider limit : surrender charge applies to entire withdrawal
    if wd_rider_applies:
        if rider_waived_charges:
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
        "AccountValue": post_av,
        "PenaltyFreeWithdrawalBalance": post_pfwb,
        "PenaltyFreeWithdrawalAmount": calculated_penalty_free_withdrawal_amount,
        "EnhancedPenaltyFreeWithdrawalAmount": calculated_enhanced_penalty_free_withdrawal_amount,
        "Free_Withdrawal_Amount": free_withdrawal_amount,
        "WithdrawalCount_ContractYear": post_withdrawal_count,
        "Withdrawal_Count": post_withdrawal_count,
        "PriorYear_RiderWithdrawalUsed": prior_year_rider_used_code,
        # Debug / audit fields recorded in calc block
        "_mva_excess_amount": mva_charge_amount,
        "_mva_rate_at_start": rate_at_start,
        "_mva_rate_current": rate_current,
        "_mva_remaining_months": remaining_months,
        "_mva_waived": mva_waived,
        "_iwr_selected": has_interest_withdrawal_rider,
        "_iwr_applies": iwr_applies,
        "_iwr_policy_year": wd_policy_year,
        "_iwr_tax_qualified": tax_qualified,
        "_iwr_free_amount_a": accum_interest,
        "_iwr_free_amount_b": rmd_component,
        "_iwr_free_withdrawal_amount": free_withdrawal_amount,
        "_iwr_waived_charges": iwr_applies and rider_waived_charges,
        "_wd_rider_for_waiver": wd_rider_for_waiver,
        "_wd_rider_conflict": wd_rider_conflict,
        "_wd_rider_applies": wd_rider_applies,
        "_wd_first_withdrawal_contract_year": is_first_withdrawal_contract_year,
        "_wd_within_guarantee_period": within_guarantee_period,
        "_wd_in_right_to_examine_period": in_right_to_examine_period,
        "_wd_charge_free_limit": charge_free_limit,
        "_wd_charge_free_limit_label": charge_free_limit_label,
        "_wd_rider_limit_amount": rider_limit_amount,
        "_wd_rider_waived_charges": rider_waived_charges,
        "_wd_policy_year": wd_policy_year,
        "_wd_penalty_free_base_amount": penalty_free_base_amount,
        "_wd_penalty_free_base_source": penalty_free_base_source,
        "_lbr_percentage": lbr_percentage,
        "_lbr_limit_a": lbr_limit_a,
        "_lbr_limit_b": lbr_limit_b,
        "_lbr_limit": lbr_limit,
        "_elbr_regular_percentage": lbr_percentage,
        "_elbr_enhanced_percentage": elbr_enhanced_percentage,
        "_elbr_regular_limit_a": elbr_regular_limit_a,
        "_elbr_regular_limit_b": elbr_regular_limit_b,
        "_elbr_regular_limit": elbr_regular_limit,
        "_elbr_enhanced_limit_a": elbr_enhanced_limit_a,
        "_elbr_enhanced_limit_b": elbr_enhanced_limit_b,
        "_elbr_enhanced_limit": elbr_enhanced_limit,
        "_elbr_limit_mode": elbr_limit_mode if wd_rider_for_waiver == "ELBR" else None,
        "_elbr_limit_used": elbr_limit if wd_rider_for_waiver == "ELBR" else None,
        "_elbr_prior_year_rider_used": prior_year_rider_used,
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
