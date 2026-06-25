import argparse
import csv
import os
import re
from typing import Iterable, List, Optional

import pandas as pd


DETECTOR_VERSION = "2026-06-25-v4-csv-reader-no-duplicate-exit-deadperiod"


ANOMALY_COLUMNS = [
    "row_number",
    "related_row_numbers",
    "location",
    "ticket_id",
    "severity",
    "anomaly_type",
    "description",
    "suggested_fix",
]


EXPECTED_HEADERS = [
    "Location",
    "Ticket#",
    "License Plate No.",
    "Amount",
    "Tax",
    "Fee",
    "Per Day Fee",
    "Duration(hh:mm)",
    "Entry Time",
    "Exit Time",
    "Transaction Time",
    "Ticket Status",
    "Transaction Mode",
    "Ticket Type",
    "Transaction Description",
    "Payment Status",
    "Extended By",
    "Reason",
]


def normalize_column_name(col):
    col = str(col).strip().lower()
    col = re.sub(r"[^a-z0-9]+", "_", col)
    return col.strip("_")


def find_column(df, possible_names):
    for name in possible_names:
        normalized = normalize_column_name(name)
        if normalized in df.columns:
            return normalized
    return None


def parse_money(series):
    return (
        series.astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.strip()
        .replace({"": None, "nan": None, "None": None})
        .pipe(pd.to_numeric, errors="coerce")
    )


def parse_duration_to_hours(value):
    """
    Handles values like:
    1
    2
    1:30
    01:30
    1h
    1.5

    Returns None for values like 'Until 11 PM'.
    """
    if pd.isna(value):
        return None

    text = str(value).strip().lower()

    if text == "":
        return None

    if "until" in text:
        return None

    text = (
        text.replace("hours", "")
        .replace("hour", "")
        .replace("hrs", "")
        .replace("hr", "")
        .replace("h", "")
        .strip()
    )

    if ":" in text:
        parts = text.split(":")
        if len(parts) == 2:
            try:
                hours = float(parts[0])
                minutes = float(parts[1])
                return hours + minutes / 60
            except ValueError:
                return None

    try:
        return float(text)
    except ValueError:
        return None


def _parse_csv_line(line: str, delimiter: str) -> List[str]:
    """Parse one report row using csv.reader so quoted commas are handled correctly."""
    return next(
        csv.reader(
            [line],
            delimiter=delimiter,
            quotechar='"',
            skipinitialspace=False,
        )
    )


def _find_header_row(lines: List[str]):
    """Return (header_index, delimiter, headers)."""
    delimiters_to_try = ["\t", ","]

    for i, line in enumerate(lines):
        for delimiter in delimiters_to_try:
            try:
                cells = [cell.strip() for cell in _parse_csv_line(line, delimiter)]
            except csv.Error:
                continue

            if len(cells) >= 2 and cells[0] == "Location" and cells[1].startswith("Ticket"):
                while cells and cells[-1] == "":
                    cells.pop()
                return i, delimiter, cells

    raise ValueError(
        "Could not find the real header row. Expected a row starting with Location and Ticket#."
    )


def read_parking_report(input_csv):
    """
    Reads SafetyPark-style parking reports.

    Supported formats:
    - First line may be a report title/date range.
    - A later line contains the actual headers starting with Location and Ticket#.
    - Rows may be tab-separated or comma-separated.
    - Quoted commas inside fields, such as "5 Dudley ave, Venice", are handled correctly.
    """
    encodings_to_try = ["utf-8-sig", "utf-8", "cp1252"]
    lines = None
    last_error = None

    for encoding in encodings_to_try:
        try:
            with open(input_csv, "r", encoding=encoding, newline="") as file:
                lines = file.read().splitlines()
            break
        except Exception as error:
            last_error = error

    if lines is None:
        raise ValueError(f"Could not read file. Last error: {last_error}")

    header_index, delimiter, headers = _find_header_row(lines)

    if not headers:
        raise ValueError("Found a header row, but it did not contain any usable columns.")

    rows = []
    malformed_rows = []

    # Real report line numbers are 1-based. If header_index is 1, first data row is 3.
    for source_line_number, line in enumerate(lines[header_index + 1 :], start=header_index + 2):
        if line.strip() == "":
            continue

        try:
            values = [value.strip() for value in _parse_csv_line(line, delimiter)]
        except csv.Error as error:
            malformed_rows.append(
                {
                    "line_number": source_line_number,
                    "problem": "csv_parse_error",
                    "error": str(error),
                    "raw_line": line,
                }
            )
            continue

        # Remove extra blank values caused by trailing tabs/commas.
        while len(values) > len(headers) and values[-1] == "":
            values.pop()

        malformed_problem = ""
        malformed_extra_values = ""

        if len(values) < len(headers):
            malformed_problem = "too_few_columns"
            values = values + [""] * (len(headers) - len(values))

        elif len(values) > len(headers):
            malformed_problem = "too_many_columns"
            malformed_extra_values = repr(values[len(headers) :])
            values = values[: len(headers)]

        row = dict(zip(headers, values))
        row["_source_line_number"] = source_line_number
        row["_malformed_problem"] = malformed_problem
        row["_malformed_extra_values"] = malformed_extra_values
        rows.append(row)

        if malformed_problem:
            malformed_rows.append(
                {
                    "line_number": source_line_number,
                    "problem": malformed_problem,
                    "extra_values": malformed_extra_values,
                }
            )

    df = pd.DataFrame(rows)
    df.attrs["malformed_rows"] = malformed_rows
    df.attrs["delimiter"] = "tab" if delimiter == "\t" else "comma"
    return df


def add_anomaly(
    anomalies,
    row,
    severity,
    anomaly_type,
    description,
    suggested_fix,
    related_row_numbers=None,
):
    source_row_number = row.get("row_number")

    if related_row_numbers is None:
        related_row_numbers = [source_row_number]

    cleaned_related_row_numbers = []

    for row_number in related_row_numbers:
        if pd.isna(row_number):
            continue

        try:
            cleaned_related_row_numbers.append(int(row_number))
        except (ValueError, TypeError):
            cleaned_related_row_numbers.append(row_number)

    anomalies.append(
        {
            "row_number": source_row_number,
            "related_row_numbers": ", ".join(
                str(row_number) for row_number in cleaned_related_row_numbers
            ),
            "location": row.get("location"),
            "ticket_id": row.get("ticket_id"),
            "severity": severity,
            "anomaly_type": anomaly_type,
            "description": description,
            "suggested_fix": suggested_fix,
        }
    )


def _safe_not_blank(series):
    return series.notna() & (series.astype(str).str.strip() != "")


def detect_anomalies(input_csv):
    df = read_parking_report(input_csv)

    original_columns = [col for col in list(df.columns) if not str(col).startswith("_")]
    df.columns = [normalize_column_name(col) for col in df.columns]

    if "source_line_number" in df.columns:
        df["row_number"] = pd.to_numeric(df["source_line_number"], errors="coerce").astype("Int64")
    else:
        df["row_number"] = range(3, len(df) + 3)

    location_col = find_column(df, ["Location"])
    ticket_col = find_column(df, ["Ticket#", "Ticket", "Ticket ID"])
    amount_col = find_column(df, ["Amount"])
    duration_col = find_column(df, ["Duration(hh:mm)", "Duration", "Duration hh:mm"])
    entry_col = find_column(df, ["Entry Time", "Entry"])
    transaction_col = find_column(df, ["Transaction Time"])
    payment_status_col = find_column(df, ["Payment Status"])
    ticket_status_col = find_column(df, ["Ticket Status"])
    extended_by_col = find_column(df, ["Extended By"])
    reason_col = find_column(df, ["Reason"])
    malformed_problem_col = find_column(df, ["_malformed_problem", "malformed_problem"])

    if location_col:
        df["location"] = df[location_col].astype(str).str.strip()
    else:
        df["location"] = ""

    if ticket_col:
        df["ticket_id"] = df[ticket_col].astype(str).str.strip()
    else:
        df["ticket_id"] = ""

    # Extension/overstay/edit tickets are normal SafetyPark behavior and should not be duplicate anomalies.
    df["is_extension_ticket"] = (
        df["ticket_id"]
        .astype(str)
        .str.upper()
        .str.contains(r"-(?:EXT|OS|EEX|EOS)$", regex=True, na=False)
    )

    if extended_by_col:
        df["extended_by_clean"] = df[extended_by_col].astype(str).str.strip()
        df["is_extension_ticket"] = df["is_extension_ticket"] | (df["extended_by_clean"] != "")

    if reason_col:
        df["reason_clean"] = df[reason_col].astype(str).str.strip()
        df["is_extension_ticket"] = df["is_extension_ticket"] | (df["reason_clean"] != "")

    if amount_col:
        df["amount_clean"] = parse_money(df[amount_col])
    else:
        df["amount_clean"] = pd.NA

    if duration_col:
        df["duration_raw"] = df[duration_col].astype(str).str.strip()
        df["duration_hours"] = df[duration_col].apply(parse_duration_to_hours)
    else:
        df["duration_raw"] = ""
        df["duration_hours"] = pd.NA

    if entry_col:
        df["entry_time_clean"] = pd.to_datetime(df[entry_col], errors="coerce")
    else:
        df["entry_time_clean"] = pd.NaT

    if transaction_col:
        df["transaction_time_clean"] = pd.to_datetime(df[transaction_col], errors="coerce")
    else:
        df["transaction_time_clean"] = pd.NaT

    if payment_status_col:
        df["payment_status_clean"] = df[payment_status_col].astype(str).str.lower().str.strip()
    else:
        df["payment_status_clean"] = ""

    if ticket_status_col:
        df["ticket_status_clean"] = df[ticket_status_col].astype(str).str.lower().str.strip()
    else:
        df["ticket_status_clean"] = ""

    anomalies = []

    # 1. Malformed rows from parsing problems. These are real data-read issues.
    if malformed_problem_col:
        malformed_mask = df[malformed_problem_col].astype(str).str.strip() != ""
        for _, row in df[malformed_mask].iterrows():
            add_anomaly(
                anomalies,
                row,
                "high",
                "malformed_row",
                f"This row had a parsing issue: {row.get(malformed_problem_col)}.",
                "Check whether the export row has the right number of columns or broken quoting.",
            )

    # 2. Missing location
    for _, row in df[df["location"].astype(str).str.strip() == ""].iterrows():
        add_anomaly(
            anomalies,
            row,
            "high",
            "missing_location",
            "This row is missing a parking lot location.",
            "Check the original export and fill in the correct location.",
        )

    # 3. Missing ticket ID
    for _, row in df[df["ticket_id"].astype(str).str.strip() == ""].iterrows():
        add_anomaly(
            anomalies,
            row,
            "high",
            "missing_ticket_id",
            "This row is missing a ticket ID.",
            "Check whether this transaction was exported correctly.",
        )

    # Duplicate ticket IDs are intentionally NOT flagged.
    # In SafetyPark reports, repeated IDs usually represent extension, overstay, or edit rows.

    # Exit-before-entry is intentionally NOT flagged.
    # Most prior hits were parser alignment issues, and many reports have blank Exit Time.

    # Dead periods are intentionally NOT flagged.
    # Long gaps are normal for low-volume lots or off-hours.

    # 4. Missing or invalid entry time
    for _, row in df[df["entry_time_clean"].isna()].iterrows():
        # Avoid spamming invalid-entry-time for already malformed rows.
        if str(row.get("malformed_problem", "")).strip() != "":
            continue
        add_anomaly(
            anomalies,
            row,
            "high",
            "invalid_entry_time",
            "Entry time is missing or could not be parsed.",
            "Fix the entry time format or recover the value from the source system.",
        )

    # 5. Negative amount
    negative_amount = df["amount_clean"].notna() & (df["amount_clean"] < 0)

    for _, row in df[negative_amount].iterrows():
        add_anomaly(
            anomalies,
            row,
            "high",
            "negative_amount",
            f"Amount is negative: {row['amount_clean']}.",
            "Check whether this is a refund, correction, or data error.",
        )

    # 6. Successful payment with zero or missing amount
    # Exclude extension/edit rows because they may be legitimate adjustments.
    successful_zero_amount = (
        df["payment_status_clean"].str.contains("succeeded", na=False)
        & (~df["is_extension_ticket"])
        & (df["amount_clean"].isna() | (df["amount_clean"] <= 0))
    )

    for _, row in df[successful_zero_amount].iterrows():
        add_anomaly(
            anomalies,
            row,
            "medium",
            "successful_payment_zero_amount",
            "Payment status says succeeded, but amount is missing or zero.",
            "Check whether this was free parking, validation, or a payment export issue.",
        )

    # 7. Weird duration values
    if duration_col:
        weird_duration = (
            _safe_not_blank(df[duration_col])
            & df["duration_hours"].isna()
            & ~df[duration_col].astype(str).str.lower().str.contains("until", na=False)
        )

        for _, row in df[weird_duration].iterrows():
            add_anomaly(
                anomalies,
                row,
                "low",
                "unparsed_duration",
                f"Duration value could not be parsed: {row[duration_col]}.",
                "Add a parser rule for this duration format or correct the value manually.",
            )

    # 8. Suspiciously long duration
    suspicious_duration = df["duration_hours"].notna() & (df["duration_hours"] > 24)

    for _, row in df[suspicious_duration].iterrows():
        add_anomaly(
            anomalies,
            row,
            "medium",
            "suspicious_duration",
            f"Duration is unusually long: {row['duration_hours']} hours.",
            "Check whether this is a daily ticket, overnight ticket, or a bad duration value.",
        )

    # 9. Same exact location, exact entry time, same duration, different prices.
    # This is intentionally exact-time only, not same-hour.
    price_check = df[
        _safe_not_blank(df["location"])
        & df["entry_time_clean"].notna()
        & df["duration_hours"].notna()
        & df["amount_clean"].notna()
        & (~df["is_extension_ticket"])
    ].copy()

    if not price_check.empty:
        grouped = price_check.groupby(
            ["location", "entry_time_clean", "duration_hours"], dropna=False
        )["amount_clean"].nunique()
        bad_groups = grouped[grouped > 1].reset_index()

        for _, bad_group in bad_groups.iterrows():
            matches = price_check[
                (price_check["location"] == bad_group["location"])
                & (price_check["entry_time_clean"] == bad_group["entry_time_clean"])
                & (price_check["duration_hours"] == bad_group["duration_hours"])
            ]

            related_rows = matches["row_number"].tolist()

            for _, row in matches.iterrows():
                add_anomaly(
                    anomalies,
                    row,
                    "high",
                    "same_exact_time_duration_different_prices",
                    (
                        "Same location, exact entry time, and same duration have multiple prices. "
                        f"Location: {row['location']}, entry time: {row['entry_time_clean']}, "
                        f"duration: {row['duration_hours']}."
                    ),
                    "Check whether one of the prices is incorrect or whether these records represent a special case.",
                    related_row_numbers=related_rows,
                )

    anomaly_df = pd.DataFrame(anomalies, columns=ANOMALY_COLUMNS)

    return anomaly_df, original_columns


def main():
    parser = argparse.ArgumentParser(description="Detect anomalies in parking transaction CSV files.")
    parser.add_argument("--input", required=True, help="Path to input parking CSV")
    parser.add_argument("--output", default="reports/anomaly_report.csv", help="Path to output anomaly report CSV")

    args = parser.parse_args()

    anomaly_df, original_columns = detect_anomalies(args.input)

    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    anomaly_df.to_csv(args.output, index=False)

    print("Parking Data Anomaly Detector")
    print("=============================")
    print(f"Detector version: {DETECTOR_VERSION}")
    print(f"Input file: {args.input}")
    print(f"Output file: {args.output}")
    print()
    print(f"Original columns found: {', '.join(original_columns)}")
    print(f"Total anomalies found: {len(anomaly_df)}")

    if len(anomaly_df) > 0:
        print()
        print("Anomalies by type:")
        print(anomaly_df["anomaly_type"].value_counts())
        print()
        print("Anomalies by severity:")
        print(anomaly_df["severity"].value_counts())


if __name__ == "__main__":
    main()

