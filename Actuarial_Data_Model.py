"""
Actuarial_Data_Model.py
-----------------------
Main orchestrator for the MYGA/FIA actuarial engine.

Responsibility
--------------
  1. Prompt the user for input / output paths and reference tables path.
  2. Load lookup tables (ProductTables, SurrenderCharges, MVA_Table)
     from the reference tables workbook.
  3. Read the PolicyData sheet from the input workbook and process all rows.
  4. Call event_1.process_initialization -> EventOutput (Event 1).
  5. Detect whether a withdrawal event exists on the same row.
  6. If yes:
       a. Roll the EOD state forward to the next valuation date.
       b. Call event_2.process_withdrawal -> EventOutput (Event 2).
  7. Write the production output as multiple policy rows.
  8. Write the original transposed output only when audit mode is on.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from config import (
    FIELDS,
    FIELD_DOMAIN,
    MVA_DATE_COLUMN,
    MVA_RATE_COLUMNS,
    AUDIT_MODE,
    AUDIT_SELECTED_POLICIES,
    ANNUITIZATION_SWITCH,
)
from models import EventOutput
from utils import to_pct, fmt_date, fmt_output
from valuation import roll_forward
from events.event_1 import process_initialization
from events.event_2 import extract_event2_input, process_withdrawal
from events.annuitization import AnnuityEngine, process_annuitization


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


def append_annuitization_to_policy(
    row: pd.Series,
    base_eod: Dict[str, Any],
    col_specs: List[Tuple[str, Dict[str, Any]]],
    annuity_engine: AnnuityEngine,
    product_tables: pd.DataFrame,
) -> Tuple[Dict[str, Any], List[Tuple[str, Dict[str, Any]]]]:
    """
    Run the stand-alone annuitization calculation on top of the existing
    daily policy result and append the Event 4 blocks to the audit layout.
    """
    ann_output: EventOutput = process_annuitization(
        row=row,
        base_state=base_eod,
        engine=annuity_engine,
        product_tables=product_tables,
    )

    ann_date = fmt_date(ann_output.eod.get("ValuationDate"))
    new_col_specs = list(col_specs)
    new_col_specs.extend(ann_output.as_col_specs(ann_date))

    return ann_output.eod, new_col_specs


# ---------------------------------------------------------------------------
# Workbook loaders
# ---------------------------------------------------------------------------

def load_product_tables(xls: pd.ExcelFile) -> pd.DataFrame:
    """
    Read the ``ProductTables`` sheet and return one normalised DataFrame.

    The sheet may contain multiple repeated 4-column blocks across the tab,
    each with headers:
        TableName | ProductType | Value | Effective Date
    """
    if "ProductTables" not in xls.sheet_names:
        return pd.DataFrame(columns=["TableName", "ProductType", "Value", "EffectiveDate"])

    raw = pd.read_excel(
        xls,
        sheet_name="ProductTables",
        engine="openpyxl",
        header=None,
    )

    blocks = []

    def _norm_header(x: Any) -> str:
        s = "" if pd.isna(x) else str(x).strip()
        s = s.replace("\n", " ")
        s = " ".join(s.split())
        return s.lower()

    ncols = raw.shape[1]

    for start_col in range(0, ncols - 3):
        headers = [_norm_header(raw.iat[1, start_col + i]) for i in range(4)]
        if headers == ["tablename", "producttype", "value", "effective date"]:
            block = raw.iloc[2:, start_col:start_col + 4].copy()
            block.columns = ["TableName", "ProductType", "Value", "EffectiveDate"]
            blocks.append(block)

    if not blocks:
        raise ValueError(
            "ProductTables tab does not contain any valid 4-column blocks with headers: "
            "TableName, ProductType, Value, Effective Date."
        )

    df = pd.concat(blocks, ignore_index=True)

    df["TableName"] = df["TableName"].astype(str).str.strip()
    df["ProductType"] = df["ProductType"].astype(str).str.strip()
    df["EffectiveDate"] = pd.to_datetime(df["EffectiveDate"], errors="coerce")

    df = df[
        df["TableName"].ne("")
        & df["ProductType"].ne("")
        & df["TableName"].ne("nan")
        & df["ProductType"].ne("nan")
        & df["Value"].notna()
        & df["EffectiveDate"].notna()
    ].copy()

    df = df.sort_values(
        ["TableName", "ProductType", "EffectiveDate"]
    ).reset_index(drop=True)

    return df


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
    """
    if "MVA_Table" not in xls.sheet_names:
        return pd.DataFrame()

    df = pd.read_excel(xls, sheet_name="MVA_Table", engine="openpyxl")

    if MVA_DATE_COLUMN not in df.columns:
        raise ValueError("MVA_Table is missing required column 'MDATE'.")

    available_rate_cols = [col for col in MVA_RATE_COLUMNS if col in df.columns]
    if not available_rate_cols:
        raise ValueError("MVA_Table does not contain any supported MVA rate columns.")

    df = df[[MVA_DATE_COLUMN] + available_rate_cols].copy()
    df[MVA_DATE_COLUMN] = pd.to_datetime(df[MVA_DATE_COLUMN], errors="coerce")
    df = df[df[MVA_DATE_COLUMN].notna()].copy()
    df = df.set_index(MVA_DATE_COLUMN).sort_index()

    for col in available_rate_cols:
        df[col] = df[col].map(to_pct)

    return df


def find_policy_sheet(xls: pd.ExcelFile) -> str:
    """
    Return the input policy sheet name.

    The input workbook is expected to contain only the PolicyData tab.
    """
    if "PolicyData" not in xls.sheet_names:
        raise ValueError("Input workbook must contain a 'PolicyData' sheet.")
    return "PolicyData"


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def build_model_df(
    col_specs: List[Tuple[str, Optional[Dict[str, Any]]]],
) -> pd.DataFrame:
    """
    Build the original transposed model output table.

    Layout
    ------
    - Rows  = output fields (from ``FIELDS`` in config.py)
    - Cols  = event/valuation blocks supplied in *col_specs*

    The first two columns are always ``Field`` and ``Domain``.
    """
    rows = []
    for field in FIELDS:
        row: Dict[str, Any] = {
            "Field": field,
            "Domain": FIELD_DOMAIN.get(field, ""),
        }
        for col_name, block in col_specs:
            value = block.get(field) if block else None
            row[col_name] = fmt_output(value, field)
        rows.append(row)

    return pd.DataFrame(rows)


def write_model(
    col_specs: List[Tuple[str, Optional[Dict[str, Any]]]],
    output_path: str,
) -> None:
    """
    Write the original transposed model output to Excel.
    """
    build_model_df(col_specs).to_excel(output_path, index=False, engine="openpyxl")


def write_production_output(
    output_rows: List[Dict[str, Any]],
    output_path: str,
) -> None:
    """
    Write the production output as one row per policy using the standard field list.
    """
    rows = []
    for state in output_rows:
        row = {field: fmt_output(state.get(field), field) for field in FIELDS}
        rows.append(row)

    pd.DataFrame(rows, columns=FIELDS).to_excel(output_path, index=False, engine="openpyxl")


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


def derive_audit_path(output_path: str) -> str:
    base, ext = os.path.splitext(output_path)
    return f"{base}_audit{ext}"


def clean_sheet_name(name: str, fallback_index: int) -> str:
    """
    Build a valid Excel sheet name (<= 31 chars, no invalid characters).
    """
    bad_chars = set(r'[]:*?/\\')
    cleaned = "".join("_" if c in bad_chars else c for c in str(name))
    cleaned = cleaned.strip() or f"Policy_{fallback_index}"
    return cleaned[:31]


# ---------------------------------------------------------------------------
# Single-policy processor
# ---------------------------------------------------------------------------

def process_single_policy(
    row: pd.Series,
    product_tables: pd.DataFrame,
    surrender_charges: pd.DataFrame,
    rates_df: pd.DataFrame,
) -> Tuple[Dict[str, Any], List[Tuple[str, Dict[str, Any]]]]:
    """
    Process one policy row and return:
      - final EOD state for production output
      - original transposed column specs for audit output
    """
    # ------------------------------------------------------------------
    # 1. Event 1 — initialize from input row
    # ------------------------------------------------------------------
    event1_output: EventOutput = process_initialization(
        row,
        surrender_charges,
        product_tables,
        rates_df=rates_df,
    )

    eod1_date = fmt_date(event1_output.eod.get("ValuationDate"))
    col_specs: List[Tuple[str, Dict[str, Any]]] = list(
        event1_output.as_col_specs(eod1_date)
    )

    # ------------------------------------------------------------------
    # 2. Always roll forward to the next valuation date
    # ------------------------------------------------------------------
    event1_val_date = pd.to_datetime(
        event1_output.eod.get("ValuationDate"),
        errors="coerce",
    )
    if pd.isna(event1_val_date):
        raise ValueError("Event 1 valuation date is missing.")

    next_val_date = event1_val_date + pd.Timedelta(days=1)

    valuation_state = roll_forward(
        event1_output.eod,
        surrender_charges,
        target_date=next_val_date,
    )

    valuation_date = fmt_date(valuation_state.get("ValuationDate"))
    col_specs.append((f"Valuation {valuation_date}", valuation_state))

    # Default production output = next-day valuation state
    final_eod = valuation_state

    # ------------------------------------------------------------------
    # 3. Event 2 — PartialWithdrawal (optional, on next_val_date)
    # ------------------------------------------------------------------
    event2_input = extract_event2_input(row)
    if event2_input is not None:
        event2_input["Valuation Date"] = next_val_date

        # # --- TEMP DEBUG START ---
        # prior_day = pd.to_datetime(next_val_date) - pd.Timedelta(days=1)
        # print("\n[MVA DEBUG] ------------------------------")
        # print("[MVA DEBUG] PolicyNumber:", valuation_state.get("PolicyNumber"))
        # print("[MVA DEBUG] Valuation Date:", next_val_date)
        # print("[MVA DEBUG] Prior day:", prior_day)
        # print("[MVA DEBUG] GrossWD input:", event2_input.get("GrossWD"))
        # print("[MVA DEBUG] PFWB before WD:", valuation_state.get("PenaltyFreeWithdrawalBalance"))
        # print("[MVA DEBUG] GP Start:", valuation_state.get("GuaranteePeriodStartDate"))
        # print("[MVA DEBUG] GP End:", valuation_state.get("GuaranteePeriodEndDate"))
        # print("[MVA DEBUG] MVA start rate A:", valuation_state.get("MVAReferenceRateAtStart"))
        # print("[MVA DEBUG] rates_df empty?:", rates_df.empty)

        # if not rates_df.empty:
        #     for d in [prior_day, prior_day - pd.Timedelta(days=1), prior_day - pd.Timedelta(days=2)]:
        #         print(f"[MVA DEBUG] date {d} in rates_df.index? ->", d in rates_df.index)
        #         if d in rates_df.index:
        #             print("[MVA DEBUG] row values:")
        #             print(rates_df.loc[d])
        # # --- TEMP DEBUG END ---

        event2_output: EventOutput = process_withdrawal(
            valuation_state,
            event2_input,
            surrender_charges,
            rates_df=rates_df,
        )
        eod2_date = fmt_date(event2_output.eod.get("ValuationDate"))
        col_specs.extend(event2_output.as_col_specs(eod2_date))

        final_eod = event2_output.eod

    return final_eod, col_specs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Entry point — read inputs, run all policies, write outputs.
    """
    input_path = prompt_path("Enter input Excel file", "policy_input.xlsx")
    reference_path = prompt_path("Enter reference tables Excel file", "reference_tables.xlsx")
    output_path = prompt_path("Enter output Excel file", "policy_output.xlsx")

    input_xls = pd.ExcelFile(input_path, engine="openpyxl")
    reference_xls = pd.ExcelFile(reference_path, engine="openpyxl")

    product_tables = load_product_tables(reference_xls)
    surrender_charges = load_surrender_charges(reference_xls)
    rates_df = load_mva_rates(reference_xls)

    policy_sheet = find_policy_sheet(input_xls)
    policy_df = pd.read_excel(input_xls, sheet_name=policy_sheet, engine="openpyxl")
    if policy_df.empty:
        raise ValueError(f"'{policy_sheet}' sheet is empty.")

    annuity_engine = AnnuityEngine(reference_xls) if ANNUITIZATION_SWITCH == "on" else None

    production_rows: List[Dict[str, Any]] = []
    audit_specs: List[Tuple[Any, List[Tuple[str, Dict[str, Any]]]]] = []

    for _, row in policy_df.iterrows():
        final_eod, col_specs = process_single_policy(
            row,
            product_tables,
            surrender_charges,
            rates_df,
        )

        if ANNUITIZATION_SWITCH == "on":
            final_eod, col_specs = append_annuitization_to_policy(
                row=row,
                base_eod=final_eod,
                col_specs=col_specs,
                annuity_engine=annuity_engine,
                product_tables=product_tables,
            )

        production_rows.append(final_eod)

        policy_number = final_eod.get("PolicyNumber")
        if should_audit_policy(policy_number, AUDIT_MODE, AUDIT_SELECTED_POLICIES):
            audit_specs.append((policy_number, col_specs))

    write_production_output(production_rows, output_path)
    print(f"Done. Production output saved to: {output_path}")

    if AUDIT_MODE != "none" and audit_specs:
        audit_path = derive_audit_path(output_path)
        with pd.ExcelWriter(audit_path, engine="openpyxl") as writer:
            for i, (policy_number, col_specs) in enumerate(audit_specs, start=1):
                sheet_name = clean_sheet_name(f"Policy_{policy_number}", i)
                build_model_df(col_specs).to_excel(
                    writer,
                    sheet_name=sheet_name,
                    index=False,
                )
        print(f"Audit output saved to: {audit_path}")


if __name__ == "__main__":
    main()