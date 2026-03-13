import os
import pandas as pd

# Import the field list and field categories from config.py.
# These control which rows appear in the final output file.
from config import FIELDS, FIELD_DOMAIN

# Import the main business-logic functions from engine.py.
# These handle rate conversion, date formatting, initialization,
# roll-forward valuation, Event2 processing, and Event2 detection.
from engine import (
    to_pct,
    fmt_date,
    fmt_output,
    process_initialization,
    roll_forward,
    process_withdrawal,
    extract_event2_input,
)


# Ask the user for a file path.
# If the user presses Enter, use the default path instead.
# Also clean off any quote marks and convert the result to a full absolute path.
def prompt_path(prompt, default):
    p = input(f"{prompt} [Default: {default}]: ").strip()
    return os.path.abspath((p or default).strip('"').strip("'"))


# Read the ProductTables sheet and turn it into a nested dictionary.
# Example result:
# {
#   "CreditingRate": {"5-year": "5.75%"},
#   "ContractCharge": {"Annual": 0}
# }
# If the workbook does not contain a ProductTables sheet, return an empty dictionary.
def load_product_tables(xls):
    if "ProductTables" not in xls.sheet_names:
        return {}

    # Try reading the sheet normally first.
    df = pd.read_excel(xls, sheet_name="ProductTables", engine="openpyxl")

    # If the expected header row is missing, try again by skipping one row.
    if "TableName" not in df.columns:
        df = pd.read_excel(xls, sheet_name="ProductTables", skiprows=1, engine="openpyxl")

    tables = {}
    for _, row in df.iterrows():
        table_name = str(row.get("TableName", "")).strip()
        key = str(row.get("Key", "")).strip()

        # Only keep rows that have a valid table name and key.
        if table_name and key and table_name != "nan":
            tables.setdefault(table_name, {})[key] = row.get("Value")

    return tables


# Read the SurrenderCharges sheet and standardize its columns.
# The output is a DataFrame with:
# - Year
# - ChargeRate (converted to decimal format)
# If the workbook does not contain this sheet, return an empty DataFrame.
def load_surrender_charges(xls):
    if "SurrenderCharges" not in xls.sheet_names:
        return pd.DataFrame(columns=["Year", "ChargeRate"])

    # Try reading the sheet normally first.
    df = pd.read_excel(xls, sheet_name="SurrenderCharges", engine="openpyxl")

    # If the expected header row is missing, try again by skipping one row.
    if "Year" not in df.columns:
        df = pd.read_excel(xls, sheet_name="SurrenderCharges", skiprows=1, engine="openpyxl")

    # Convert Year to numeric and ChargeRate to decimal percentage.
    df["Year"] = pd.to_numeric(df["Year"], errors="coerce")
    df["ChargeRate"] = df["ChargeRate"].map(to_pct)
    return df


# Decide which sheet contains the policy input row.
# Prefer a sheet named PolicyData.
# If that does not exist, use the first sheet that is not a lookup sheet.
def find_policy_sheet(xls):
    if "PolicyData" in xls.sheet_names:
        return "PolicyData"

    excluded = {"ProductTables", "SurrenderCharges"}
    candidates = [s for s in xls.sheet_names if s not in excluded]
    if not candidates:
        raise ValueError("No policy input sheet found.")
    return candidates[0]


# Build the final output table and write it to Excel.
# Each model field becomes one row.
# Each event block / valuation block becomes one column.
def write_model(col_specs, output_path):
    rows = []

    for field in FIELDS:
        # Start each row with the field name and its domain/category.
        row = {
            "Field": field,
            "Domain": FIELD_DOMAIN.get(field, ""),
        }

        # For each output block, pull the value for this field
        # and format it for Excel output.
        for col_name, block in col_specs:
            value = block.get(field) if block else None
            row[col_name] = fmt_output(value, field)

        rows.append(row)

    pd.DataFrame(rows).to_excel(output_path, index=False, engine="openpyxl")


# Main driver of the script.
# This reads the input file, builds Event1, optionally builds Event2,
# and writes the final output workbook.
def main():
    # Ask the user for the input and output file locations.
    input_path = prompt_path("Enter input Excel file", "policy_input.xlsx")
    output_path = prompt_path("Enter output Excel file", "policy_model_output.xlsx")

    # Open the Excel workbook once so multiple sheets can be read from it.
    xls = pd.ExcelFile(input_path, engine="openpyxl")

    # Load lookup tables used by the calculations.
    product_tables = load_product_tables(xls)
    surrender_charges = load_surrender_charges(xls)

    # Find and read the sheet containing the policy row.
    policy_sheet = find_policy_sheet(xls)
    policy_df = pd.read_excel(xls, sheet_name=policy_sheet, engine="openpyxl")

    if policy_df.empty:
        raise ValueError(f"{policy_sheet} sheet is empty.")

    # For now, the model processes only the first policy row.
    init_row = policy_df.iloc[0]

    # Build Event1:
    # - raw Event1 data
    # - Event1 calculations
    # - Event1 validations
    # - end-of-day state after Event1
    data1, calc1, val1, eod1 = process_initialization(
        init_row,
        surrender_charges,
        product_tables,
    )

    # Use the Event1 valuation date in the dynamic output header.
    eod1_date = fmt_date(eod1.get("ValuationDate"))

    # Start the output column list with the Event1 block.
    col_specs = [
        ("Event1 Data", data1),
        ("Event1 Calc", calc1),
        ("Event1 Validation", val1),
        (f"EOD {eod1_date} / After Event1", eod1),
    ]

    # Check the same input row to see whether Event2 exists.
    # For now, Event2 is inferred from Gross WD.
    event2_input = extract_event2_input(init_row)

    if event2_input is not None:
        # First build the next valuation state from the Event1 end-of-day state.
        # This is the pre-Event2 state.
        valuation_state = roll_forward(
            eod1,
            surrender_charges,
            target_date=event2_input.get("Valuation Date"),
        )

        # Add the valuation block to the output.
        valuation_date = fmt_date(valuation_state.get("ValuationDate"))
        col_specs.append((f"Valuation {valuation_date}", valuation_state))

        # Apply Event2 as a withdrawal.
        # This produces:
        # - Event2 data
        # - Event2 calculations
        # - Event2 validations
        # - end-of-day state after Event2
        data2, calc2, val2, eod2 = process_withdrawal(
            valuation_state,
            event2_input,
            surrender_charges,
        )

        # Add the Event2 block to the output.
        eod2_date = fmt_date(eod2.get("ValuationDate"))
        col_specs.extend([
            ("Event2 Data", data2),
            ("Event2 Calc", calc2),
            ("Event2 Validation", val2),
            (f"EOD {eod2_date} / After Event2", eod2),
        ])

    # Write the final structured output to Excel.
    write_model(col_specs, output_path)
    print(f"Done. Output saved to: {output_path}")


if __name__ == "__main__":
    main()