"""
Actuarial_Data_Model.py
-----------------------
Main orchestrator for the MYGA/FIA actuarial engine.

Responsibility
--------------
This script is the *only* entry point that touches the file system
(input Excel workbook and output Excel workbook).  All business logic
lives in the modules it imports.

Flow
----
  1. Prompt the user for input / output paths.
  2. Load lookup tables (ProductTables, SurrenderCharges) from the workbook.
  3. Read the PolicyData sheet and select the first row.
  4. Call event_1.process_initialization → EventOutput (Event 1).
  5. Detect whether a withdrawal event exists on the same row.
  6. If yes:
       a. Roll the EOD state forward to the Event 2 valuation date.
       b. Call event_2.process_withdrawal → EventOutput (Event 2).
  7. Build the column-spec list and write the output workbook.

Extending the engine
--------------------
  - To add Event 3 (full surrender), create ``events/event_3.py`` with
    a ``process_full_surrender`` function that follows the same pattern,
    then add its call here.
  - To process multiple policy rows, wrap the event-processing block in
    a loop over ``policy_df.iterrows()``.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from config import FIELDS, FIELD_DOMAIN
from models import EventOutput
from utils import to_pct, fmt_date, fmt_output
from valuation import roll_forward
from events.event_1 import process_initialization
from events.event_2 import extract_event2_input, process_withdrawal


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


# ---------------------------------------------------------------------------
# Workbook loaders
# ---------------------------------------------------------------------------

def load_product_tables(xls: pd.ExcelFile) -> Dict[str, Any]:
    """
    Read the ``ProductTables`` sheet and return a nested dict.

    Result shape::

        {
            "CreditingRate":  {"5-year": "5.75%"},
            "ContractCharge": {"Annual": 0},
        }

    Returns ``{}`` if the sheet does not exist.
    """
    if "ProductTables" not in xls.sheet_names:
        return {}

    df = pd.read_excel(xls, sheet_name="ProductTables", engine="openpyxl")
    if "TableName" not in df.columns:
        df = pd.read_excel(
            xls, sheet_name="ProductTables", skiprows=1, engine="openpyxl"
        )

    tables: Dict[str, Any] = {}
    for _, row in df.iterrows():
        table_name = str(row.get("TableName", "")).strip()
        key = str(row.get("Key", "")).strip()
        if table_name and key and table_name != "nan":
            tables.setdefault(table_name, {})[key] = row.get("Value")
    return tables


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


def find_policy_sheet(xls: pd.ExcelFile) -> str:
    """
    Return the name of the sheet that contains the policy input rows.

    Prefers a sheet named ``PolicyData``.  Falls back to the first sheet
    that is not a lookup sheet.

    Raises
    ------
    ValueError
        If no usable sheet is found.
    """
    if "PolicyData" in xls.sheet_names:
        return "PolicyData"
    excluded = {"ProductTables", "SurrenderCharges"}
    candidates = [s for s in xls.sheet_names if s not in excluded]
    if not candidates:
        raise ValueError("No policy input sheet found in the workbook.")
    return candidates[0]


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------

def write_model(
    col_specs: List[Tuple[str, Optional[Dict[str, Any]]]],
    output_path: str,
) -> None:
    """
    Build the transposed output table and write it to Excel.

    Layout
    ------
    - Rows  = output fields (from ``FIELDS`` in config.py)
    - Cols  = event/valuation blocks supplied in *col_specs*

    The first two columns are always ``Field`` and ``Domain``.

    Parameters
    ----------
    col_specs :
        List of ``(column_header, block_dict)`` pairs.
        ``block_dict`` may be ``None`` — those cells will be blank.
    output_path :
        Destination ``.xlsx`` file path.
    """
    rows = []
    for field in FIELDS:
        row: Dict[str, Any] = {
            "Field":  field,
            "Domain": FIELD_DOMAIN.get(field, ""),
        }
        for col_name, block in col_specs:
            value = block.get(field) if block else None
            row[col_name] = fmt_output(value, field)
        rows.append(row)

    pd.DataFrame(rows).to_excel(output_path, index=False, engine="openpyxl")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Entry point — read inputs, run events, write output.
    """
    # 1. Resolve file paths
    input_path  = prompt_path("Enter input Excel file",  "policy_input.xlsx")
    output_path = prompt_path("Enter output Excel file", "policy_model_output.xlsx")

    # 2. Open workbook and load lookup tables
    xls = pd.ExcelFile(input_path, engine="openpyxl")
    product_tables    = load_product_tables(xls)
    surrender_charges = load_surrender_charges(xls)

    # 3. Read the PolicyData sheet
    policy_sheet = find_policy_sheet(xls)
    policy_df = pd.read_excel(xls, sheet_name=policy_sheet, engine="openpyxl")
    if policy_df.empty:
        raise ValueError(f"'{policy_sheet}' sheet is empty.")

    # For now, process only the first policy row.
    init_row = policy_df.iloc[0]

    # ------------------------------------------------------------------
    # 4. Event 1 — PolicyIssue
    # ------------------------------------------------------------------
    event1_output: EventOutput = process_initialization(
        init_row, surrender_charges, product_tables
    )

    eod1_date = fmt_date(event1_output.eod.get("ValuationDate"))

    # Build initial column spec from EventOutput's helper method
    col_specs: List[Tuple[str, Dict[str, Any]]] = list(
        event1_output.as_col_specs(eod1_date)
    )

    # ------------------------------------------------------------------
    # 5. Event 2 — PartialWithdrawal (optional)
    # ------------------------------------------------------------------
    event2_input = extract_event2_input(init_row)

    if event2_input is not None:
        # 5a. Roll forward to the Event 2 valuation date
        valuation_state = roll_forward(
            event1_output.eod,
            surrender_charges,
            target_date=event2_input.get("Valuation Date"),
        )
        valuation_date = fmt_date(valuation_state.get("ValuationDate"))
        col_specs.append((f"Valuation {valuation_date}", valuation_state))

        # 5b. Apply the withdrawal
        event2_output: EventOutput = process_withdrawal(
            valuation_state, event2_input, surrender_charges
        )
        eod2_date = fmt_date(event2_output.eod.get("ValuationDate"))
        col_specs.extend(event2_output.as_col_specs(eod2_date))

    # ------------------------------------------------------------------
    # 6. Write output
    # ------------------------------------------------------------------
    write_model(col_specs, output_path)
    print(f"Done. Output saved to: {output_path}")


if __name__ == "__main__":
    main()
