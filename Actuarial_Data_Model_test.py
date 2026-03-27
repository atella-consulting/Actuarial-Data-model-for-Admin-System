"""
Actuarial_Data_Model.py
-----------------------
Main orchestrator for the MYGA/FIA daily roll-forward batch engine.

Responsibility
--------------
  1. Prompt for input/output paths, target valuation date, and audit settings.
  2. Load lookup tables (SurrenderCharges, MVA_Table) from the workbook.
  3. Read all policy rows from the PolicyData sheet.
  4. Roll every policy forward to the target date, applying withdrawals where present.
  5. Write the production output (same schema as input, one row per policy).
  6. Optionally write a separate audit output file.

Design notes
------------
- Input file schema == output file schema. Each row is the prior day's
  final rolled state. The output becomes the next day's input.
- Production output is built directly from each policy's final EOD state,
  with no dependency on audit generation.
- Audit is optional: none / selected / all. For runs over 10,000 policies,
  the default is none.
- Failed policies are logged and skipped without aborting the run.
- ProductTables are not loaded -- rates are already embedded in each input
  row from the prior run. Only SurrenderCharges and MVA_Table are needed.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from config import (
    FIELDS,
    RATE_FIELDS,
    PLAN_YEARS,
    MGSV_CONTRACT_CHARGE,
    AUDIT_MODE,
    AUDIT_SELECTED_POLICIES,
    MVA_DATE_COLUMN,
    MVA_RATE_COLUMNS,
)
from calculations import resolve_mva_column
from models import EventOutput
from utils import to_ts, to_pct, sfloat, as_code, fmt_date, fmt_output, nonempty
from valuation import roll_forward
from events.event_2 import process_withdrawal


# ---------------------------------------------------------------------------
# Field classification helpers (used by row_to_eod)
# ---------------------------------------------------------------------------

_DATE_FIELDS: frozenset = frozenset({
    "ValuationDate", "IssueDate", "EffectiveDate", "AnniversaryDateNext",
    "MaturityDate", "AnnuitantDOB", "OwnerDOB",
    "GuaranteePeriodStartDate", "GuaranteePeriodEndDate",
})

_NUMERIC_FIELDS: frozenset = frozenset({
    "IssueAge", "SinglePremium", "GuaranteedMinimumAV", "Term_Period",
    "AccumulatedInterestCurrentYear", "PenaltyFreeWithdrawalBalance",
    "RemainingMonthsInGuaranteePeriod", "AccountValue",
    "SurrenderCharge", "MVA", "CashSurrenderValue", "DailyInterest",
})

_TRANSACTION_FIELDS: frozenset = frozenset({"GrossWD", "Net", "Tax"})


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def prompt_path(prompt: str, default: str) -> str:
    """
    Ask the user for a file path, falling back to *default* on Enter.
    Strips surrounding quote characters and resolves to an absolute path.
    """
    p = input(f"{prompt} [Default: {default}]: ").strip()
    return os.path.abspath((p or default).strip('"').strip("'"))


def prompt_date(prompt: str, default: pd.Timestamp) -> pd.Timestamp:
    """
    Ask the user for a date string (YYYY-MM-DD); return *default* on Enter or
    if the input cannot be parsed.
    """
    raw = input(f"{prompt} [Default: {fmt_date(default)}]: ").strip()
    if not raw:
        return default
    ts = to_ts(raw)
    if pd.isna(ts):
        print(f"  Could not parse '{raw}', using default: {fmt_date(default)}")
        return default
    return ts


def prompt_audit_settings(
    default_mode: str,
    default_selected: List[Any],
) -> Tuple[str, List[Any]]:
    """
    Prompt the user for audit mode and, when needed, the list of policy
    numbers to audit.

    Options presented
    -----------------
    1 -> none      No audit output (fast, suitable for large production runs)
    2 -> selected  Audit only the policy numbers the user specifies
    3 -> all       Audit every policy in the run
    """
    print("\nAudit options:")
    print("  [1] none      -- no audit file (default for production)")
    print("  [2] selected  -- audit only specified policy numbers")
    print("  [3] all       -- audit every policy")
    choice = input(f"Choose audit mode [Default: {default_mode}]: ").strip()

    mode_map = {"1": "none", "2": "selected", "3": "all"}
    mode = mode_map.get(choice, default_mode)

    selected: List[Any] = list(default_selected)
    if mode == "selected":
        raw = input(
            "Enter policy numbers to audit (comma-separated, e.g. 1,4,102): "
        ).strip()
        if raw:
            selected = [p.strip() for p in raw.split(",") if p.strip()]
        if not selected:
            print("  No policy numbers entered -- falling back to mode 'none'.")
            mode = "none"

    return mode, selected


# ---------------------------------------------------------------------------
# Workbook loaders
# ---------------------------------------------------------------------------

def load_surrender_charges(xls: pd.ExcelFile) -> pd.DataFrame:
    """
    Read the ``SurrenderCharges`` sheet and return a standardised DataFrame.

    Columns: ``Year`` (int), ``ChargeRate`` (decimal float).
    Returns an empty DataFrame if the sheet does not exist.
    """
    if "SurrenderCharges" not in xls.sheet_names:
        return pd.DataFrame(columns=["Year", "ChargeRate"])

    df = pd.read_excel(xls, sheet_name="SurrenderCharges", engine="openpyxl")
    if "Year" not in df.columns:
        df = pd.read_excel(
            xls, sheet_name="SurrenderCharges", skiprows=1, engine="openpyxl"
        )

    df["Year"] = pd.to_numeric(df["Year"], errors="coerce")
    df["ChargeRate"] = df["ChargeRate"].map(to_pct)
    return df



def load_mva_rates(xls: pd.ExcelFile) -> pd.DataFrame:
    """
    Read the ``MVA_Table`` sheet and return a DataFrame indexed by MDATE.

    Returns an empty DataFrame if the sheet does not exist.
    """
    if "MVA_Table" not in xls.sheet_names:
        return pd.DataFrame()

    df = pd.read_excel(xls, sheet_name="MVA_Table", engine="openpyxl")

    if MVA_DATE_COLUMN not in df.columns:
        raise ValueError("MVA_Table is missing required column 'MDATE'.")

    available = [c for c in MVA_RATE_COLUMNS if c in df.columns]
    if not available:
        raise ValueError("MVA_Table does not contain any supported rate columns.")

    df = df[[MVA_DATE_COLUMN] + available].copy()
    df[MVA_DATE_COLUMN] = pd.to_datetime(df[MVA_DATE_COLUMN], errors="coerce")
    df = df[df[MVA_DATE_COLUMN].notna()].set_index(MVA_DATE_COLUMN).sort_index()

    for col in available:
        df[col] = df[col].map(to_pct)

    return df



def find_policy_sheet(xls: pd.ExcelFile) -> str:
    """
    Return the name of the sheet that contains the policy input rows.

    Prefers a sheet named ``PolicyData``. Falls back to the first sheet that
    is not a lookup sheet. Handles simple case/space mismatches safely.

    Raises
    ------
    ValueError
        If no usable sheet is found.
    """
    normalized = {str(s).strip().lower(): s for s in xls.sheet_names}

    if "policydata" in normalized:
        return normalized["policydata"]

    excluded = {"producttables", "surrendercharges", "mva_table"}
    candidates = [orig for key, orig in normalized.items() if key not in excluded]
    if not candidates:
        raise ValueError(
            f"No policy input sheet found in the workbook. Sheets found: {xls.sheet_names}"
        )
    return candidates[0]


# ---------------------------------------------------------------------------
# Row <-> EOD conversion
# ---------------------------------------------------------------------------

def row_to_eod(row: pd.Series) -> Dict[str, Any]:
    """
    Convert an input row (prior-day rolled snapshot) to the internal EOD
    state dict expected by ``roll_forward``.

    Internal helpers (``_mva_column``, ``_mgsv_cc``) are re-derived from
    ProductType because they are not persisted in the output schema.
    """
    eod: Dict[str, Any] = {}

    for field in FIELDS:
        raw = row.get(field)
        if field in _DATE_FIELDS:
            eod[field] = to_ts(raw)
        elif field in RATE_FIELDS:
            eod[field] = to_pct(raw)
        elif field in _NUMERIC_FIELDS:
            eod[field] = sfloat(raw, 0.0) if nonempty(raw) else 0.0
        elif field in _TRANSACTION_FIELDS:
            eod[field] = sfloat(raw, 0.0) if nonempty(raw) else None
        else:
            eod[field] = raw

    # Re-derive internal helpers not stored in the output schema.
    product_type = as_code(eod.get("ProductType", ""))
    plan_years = PLAN_YEARS.get(product_type, 5)
    eod["_mva_column"] = resolve_mva_column(plan_years)
    eod["_mgsv_cc"] = MGSV_CONTRACT_CHARGE

    return eod



def eod_to_output_row(eod: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten an EOD state dict to an output row with only ``FIELDS`` columns."""
    return {field: fmt_output(eod.get(field), field) for field in FIELDS}


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------

def should_audit_policy(
    policy_number: Any,
    audit_mode: str,
    audit_selected: List[Any],
) -> bool:
    if audit_mode == "none":
        return False
    if audit_mode == "all":
        return True
    if audit_mode == "selected":
        return str(policy_number) in {str(p) for p in audit_selected}
    return False



def audit_snap(state: Dict[str, Any], policy_number: Any, step_label: str) -> Dict[str, Any]:
    """Build one audit snapshot row."""
    snap = {field: state.get(field) for field in FIELDS}
    snap["PolicyNumber"] = policy_number
    snap["AuditStep"] = step_label
    return snap


# ---------------------------------------------------------------------------
# Single-policy processor
# ---------------------------------------------------------------------------

def process_single_policy(
    row: pd.Series,
    sc_tbl: Optional[pd.DataFrame],
    rates_df: Optional[pd.DataFrame],
    target_date: pd.Timestamp,
    audit_mode: str = "none",
    audit_selected: Optional[List[Any]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Roll one policy forward to ``target_date`` and apply any pending events.

    Returns ``(final_eod, audit_steps)``.
    ``audit_steps`` is an empty list when audit is off for this policy.
    """
    if audit_selected is None:
        audit_selected = []

    policy_number = row.get("PolicyNumber")
    do_audit = should_audit_policy(policy_number, audit_mode, audit_selected)
    audit_steps: List[Dict[str, Any]] = []

    # Read GrossWD BEFORE rolling -- roll_forward clears transaction fields.
    gross_wd_raw = None
    for col in ("GrossWD", "Gross WD"):
        val = row.get(col)
        if nonempty(val):
            gross_wd_raw = val
            break
    has_withdrawal = gross_wd_raw is not None and sfloat(gross_wd_raw, 0.0) != 0.0

    # Build prior-EOD dict and roll forward.
    prior_eod = row_to_eod(row)
    valuation_state = roll_forward(prior_eod, sc_tbl, target_date=target_date)

    if do_audit:
        audit_steps.append(audit_snap(valuation_state, policy_number, "Valuation"))

    # Apply withdrawal if one was present on the input row.
    if has_withdrawal:
        event2_input = {
            "EventType": "PartialWithdrawal",
            "Gross WD": gross_wd_raw,
            "Net": row.get("Net"),
            "Tax": row.get("Tax"),
            "Valuation Date": target_date,
        }
        event2_output: EventOutput = process_withdrawal(
            valuation_state, event2_input, sc_tbl, rates_df=rates_df
        )
        final_eod = event2_output.eod

        if do_audit:
            audit_steps.append(audit_snap(event2_output.data, policy_number, "Event2_Data"))
            audit_steps.append(audit_snap(event2_output.calc, policy_number, "Event2_Calc"))
            audit_steps.append(audit_snap(final_eod, policy_number, "EOD_AfterEvent2"))
    else:
        final_eod = valuation_state
        if do_audit:
            audit_steps.append(audit_snap(final_eod, policy_number, "EOD"))

    return final_eod, audit_steps


# ---------------------------------------------------------------------------
# Batch loop
# ---------------------------------------------------------------------------

def process_all_policies(
    policy_df: pd.DataFrame,
    sc_tbl: Optional[pd.DataFrame],
    rates_df: Optional[pd.DataFrame],
    target_date: pd.Timestamp,
    audit_mode: str = "none",
    audit_selected: Optional[List[Any]] = None,
) -> Tuple[pd.DataFrame, Optional[pd.DataFrame]]:
    """
    Roll every policy row forward to ``target_date``.

    Returns ``(production_df, audit_df)``.
    ``audit_df`` is ``None`` when ``audit_mode`` is ``'none'``.
    """
    if audit_selected is None:
        audit_selected = []

    production_rows: List[Dict[str, Any]] = []
    all_audit_steps: List[Dict[str, Any]] = []
    error_count = 0

    for idx, row in policy_df.iterrows():
        policy_number = row.get("PolicyNumber", f"<row {idx}>")
        try:
            final_eod, audit_steps = process_single_policy(
                row,
                sc_tbl,
                rates_df,
                target_date,
                audit_mode=audit_mode,
                audit_selected=audit_selected,
            )
            production_rows.append(eod_to_output_row(final_eod))
            all_audit_steps.extend(audit_steps)
        except Exception as exc:
            error_count += 1
            print(f"  [ERROR] Policy {policy_number} (row {idx}): {exc}")

    total = len(policy_df)
    if error_count:
        print(
            f"\n  {total - error_count}/{total} policies processed successfully "
            f"({error_count} failed)."
        )

    production_df = (
        pd.DataFrame(production_rows, columns=FIELDS)
        if production_rows
        else pd.DataFrame(columns=FIELDS)
    )

    audit_df: Optional[pd.DataFrame] = None
    if audit_mode != "none" and all_audit_steps:
        audit_df = pd.DataFrame(all_audit_steps)

    return production_df, audit_df


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_production_output(production_df: pd.DataFrame, output_path: str) -> None:
    """
    Write the production output to an Excel file.

    The file has the same column schema as the input, with one row per
    successfully processed policy stamped with the new valuation date.
    This file becomes the next day's input.
    """
    production_df.to_excel(output_path, index=False, engine="openpyxl")



def write_audit_output(audit_df: pd.DataFrame, audit_path: str) -> None:
    """
    Write the audit output to a separate Excel file.

    Columns are the standard output fields plus ``AuditStep``, which labels
    each row (e.g. ``Valuation``, ``EOD_AfterEvent2``).
    """
    audit_df.to_excel(audit_path, index=False, engine="openpyxl")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _derive_target_date(policy_df: pd.DataFrame) -> pd.Timestamp:
    """
    Derive the default target date as the day after the latest ValuationDate
    in the input file.

    Raises ``ValueError`` if no valid date can be found.
    """
    dates = pd.to_datetime(
        policy_df.get("ValuationDate", pd.Series(dtype=str)), errors="coerce"
    ).dropna()
    if dates.empty:
        raise ValueError(
            "Cannot derive a target date: no valid ValuationDate values in input."
        )
    return dates.max() + pd.Timedelta(days=1)



def _derive_audit_path(output_path: str) -> str:
    """Return a default audit file path alongside the production output."""
    base, ext = os.path.splitext(output_path)
    return f"{base}_audit{ext}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Entry point -- prompt for run settings, process all policies, write outputs.
    """
    print("=" * 60)
    print("  MYGA/FIA Daily Roll-Forward Engine")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. File paths
    # ------------------------------------------------------------------
    input_path = prompt_path("\nEnter input Excel file", "policy_input.xlsx")
    output_path = prompt_path("Enter output Excel file", "policy_output.xlsx")

    # ------------------------------------------------------------------
    # 2. Open workbook and load lookup tables
    #    Note: ProductTables not loaded -- rates are embedded in each input row.
    # ------------------------------------------------------------------
    print(f"\nLoading workbook: {input_path}")
    xls = pd.ExcelFile(input_path, engine="openpyxl")
    surrender_charges = load_surrender_charges(xls)
    rates_df = load_mva_rates(xls)

    # ------------------------------------------------------------------
    # 3. Read all policy rows
    # ------------------------------------------------------------------
    policy_sheet = find_policy_sheet(xls)
    policy_df = pd.read_excel(xls, sheet_name=policy_sheet, engine="openpyxl")

    if policy_df.empty:
        raise ValueError(f"'{policy_sheet}' sheet is empty -- nothing to process.")

    print(f"  {len(policy_df)} policies loaded from sheet '{policy_sheet}'.")

    # ------------------------------------------------------------------
    # 4. Target valuation date
    # ------------------------------------------------------------------
    default_target = _derive_target_date(policy_df)
    target_date = prompt_date(
        "\nEnter target valuation date (YYYY-MM-DD)", default_target
    )
    print(f"  Target valuation date: {fmt_date(target_date)}")

    # ------------------------------------------------------------------
    # 5. Audit settings
    # ------------------------------------------------------------------
    audit_mode, audit_selected = prompt_audit_settings(
        AUDIT_MODE, AUDIT_SELECTED_POLICIES
    )
    print(f"  Audit mode: {audit_mode}", end="")
    if audit_mode == "selected":
        print(f"  |  Policies: {audit_selected}", end="")
    print()

    # ------------------------------------------------------------------
    # 6. Process all policies
    # ------------------------------------------------------------------
    print(f"\nProcessing {len(policy_df)} policies...")
    production_df, audit_df = process_all_policies(
        policy_df,
        surrender_charges,
        rates_df,
        target_date,
        audit_mode=audit_mode,
        audit_selected=audit_selected,
    )

    # ------------------------------------------------------------------
    # 7. Write production output
    # ------------------------------------------------------------------
    write_production_output(production_df, output_path)
    print(f"\nProduction output  ({len(production_df)} rows) --> {output_path}")

    # ------------------------------------------------------------------
    # 8. Write audit output (only if generated)
    # ------------------------------------------------------------------
    if audit_df is not None:
        audit_path = _derive_audit_path(output_path)
        write_audit_output(audit_df, audit_path)
        print(f"Audit output       ({len(audit_df)} rows) --> {audit_path}")
    else:
        print("Audit output: skipped.")

    print("\nDone.")


if __name__ == "__main__":
    main()
