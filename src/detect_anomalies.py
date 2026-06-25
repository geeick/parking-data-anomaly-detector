import argparse
import os
import re
import pandas as pd


def normalize_column_name(col):
    col = col.strip().lower()
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
    Returns NaN for values like 'Until 11 PM'.
    """
    if pd.isna(value):
        return None

    text = str(value).strip().lower()

    if text == "":
        return None

    if "until" in text:
        return None

    text = text.replace("hours", "").replace("hour", "").replace("hrs", "").replace("hr", "").replace("h", "").strip()

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

def read_parking_report(input_csv):
    """
    Reads SafetyPark-style parking reports without using pandas' CSV parser.

    SafetyPark exports look like this:
    - Line 1: report title/date range
    - Line 2: actual column headers
    - Line 3+: transaction rows
    - Columns are tab-separated

    This manual reader avoids pandas quote/parsing errors from messy real-world
    report rows. It also preserves the original report line number for each row.
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

    header_index = None
    delimiter = "\t"

    # Find the actual header row instead of assuming it is line 2.
    # This keeps the script working even if the export includes extra title rows.
    for i, line in enumerate(lines):
        tab_cells = [cell.strip() for cell in line.split("\t")]
        comma_cells = [cell.strip() for cell in line.split(",")]

        if len(tab_cells) >= 2 and tab_cells[0] == "Location" and tab_cells[1].startswith("Ticket"):
            header_index = i
            delimiter = "\t"
            break

        if len(comma_cells) >= 2 and comma_cells[0] == "Location" and comma_cells[1].startswith("Ticket"):
            header_index = i
            delimiter = ","
            break

    if header_index is None:
        raise ValueError(
            "Could not find the real header row. "
            "Expected a row starting with Location and Ticket#."
        )

    headers = [header.strip() for header in lines[header_index].split(delimiter)]

    # Remove blank columns caused by trailing tabs/commas.
    while headers and headers[-1] == "":
        headers.pop()

    if not headers:
        raise ValueError("Found a header row, but it did not contain any usable columns.")

    rows = []
    malformed_rows = []

    # Real report line numbers are 1-based.
    for source_line_number, line in enumerate(lines[header_index + 1:], start=header_index + 2):
        if line.strip() == "":
            continue

        values = [value.strip() for value in line.split(delimiter)]

        # Remove extra blank values caused by trailing tabs/commas.
        while len(values) > len(headers) and values[-1] == "":
            values.pop()

        if len(values) < len(headers):
            values = values + [""] * (len(headers) - len(values))

        elif len(values) > len(headers):
            malformed_rows.append({
                "line_number": source_line_number,
                "problem": "too_many_columns",
                "extra_values": values[len(headers):],
            })
            values = values[:len(headers)]

        row = dict(zip(headers, values))
        row["_source_line_number"] = source_line_number
        rows.append(row)

    df = pd.DataFrame(rows)
    df.attrs["malformed_rows"] = malformed_rows
    return df

def add_anomaly(anomalies, row, severity, anomaly_type, description, suggested_fix):
    anomalies.append({
        "row_number": row.get("row_number"),
        "location": row.get("location"),
        "ticket_id": row.get("ticket_id"),
        "severity": severity,
        "anomaly_type": anomaly_type,
        "description": description,
        "suggested_fix": suggested_fix,
    })


def detect_anomalies(input_csv):
    df = read_parking_report(input_csv)

    original_columns = [
        col for col in list(df.columns)
        if not str(col).startswith("_")
    ]
    df.columns = [normalize_column_name(col) for col in df.columns]

    if "source_line_number" in df.columns:
        df["row_number"] = df["source_line_number"]
    else:
        df["row_number"] = range(3, len(df) + 3)

    location_col = find_column(df, ["Location"])
    ticket_col = find_column(df, ["Ticket#", "Ticket", "Ticket ID"])
    amount_col = find_column(df, ["Amount"])
    duration_col = find_column(df, ["Duration(hh:mm)", "Duration", "Duration hh:mm"])
    entry_col = find_column(df, ["Entry Time", "Entry"])
    exit_col = find_column(df, ["Exit Time", "Exit"])
    transaction_col = find_column(df, ["Transaction Time"])
    payment_status_col = find_column(df, ["Payment Status"])
    ticket_status_col = find_column(df, ["Ticket Status"])


    if location_col:
        df["location"] = df[location_col].astype(str).str.strip()
    else:
        df["location"] = None

    if ticket_col:
        df["ticket_id"] = df[ticket_col].astype(str).str.strip()
    else:
        df["ticket_id"] = None

    if "ticket_id" in df.columns:
        df["is_extension_ticket"] = (
            df["ticket_id"]
            .astype(str)
            .str.upper()
            .str.contains(r"-(?:EXT|OS|EEX)$", regex=True, na=False)
        )
    else:
        df["is_extension_ticket"] = False

    extended_by_col = find_column(df, ["Extended By"])
    reason_col = find_column(df, ["Reason"])

    if extended_by_col:
        df["extended_by_clean"] = df[extended_by_col].astype(str).str.strip()
        df["is_extension_ticket"] = df["is_extension_ticket"] | (df["extended_by_clean"] != "")

    if reason_col:
        df["reason_clean"] = df[reason_col].astype(str).str.strip()
        df["is_extension_ticket"] = df["is_extension_ticket"] | (df["reason_clean"] != "")

    if amount_col:
        df["amount_clean"] = parse_money(df[amount_col])
    else:
        df["amount_clean"] = None

    if duration_col:
        df["duration_hours"] = df[duration_col].apply(parse_duration_to_hours)
    else:
        df["duration_hours"] = None

    if entry_col:
        df["entry_time_clean"] = pd.to_datetime(df[entry_col], errors="coerce")
    else:
        df["entry_time_clean"] = pd.NaT

    if exit_col:
        df["exit_time_clean"] = pd.to_datetime(df[exit_col], errors="coerce")
    else:
        df["exit_time_clean"] = pd.NaT

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

    # 1. Missing location
    for _, row in df[df["location"].isna() | (df["location"].astype(str).str.strip() == "")].iterrows():
        add_anomaly(
            anomalies,
            row,
            "high",
            "missing_location",
            "This row is missing a parking lot location.",
            "Check the original export and fill in the correct location.",
        )

    # 2. Missing ticket ID
    for _, row in df[df["ticket_id"].isna() | (df["ticket_id"].astype(str).str.strip() == "")].iterrows():
        add_anomaly(
            anomalies,
            row,
            "high",
            "missing_ticket_id",
            "This row is missing a ticket ID.",
            "Check whether this transaction was exported correctly.",
        )

    # 3. Duplicate ticket IDs
    if ticket_col:
        duplicate_mask = df["ticket_id"].notna() & df["ticket_id"].duplicated(keep=False)
        for _, row in df[duplicate_mask].iterrows():
            add_anomaly(
                anomalies,
                row,
                "medium",
                "duplicate_ticket_id",
                f"Ticket ID {row['ticket_id']} appears more than once.",
                "Check whether this is a duplicate row or a legitimate ticket update.",
            )

    # 4. Missing or invalid entry time
    for _, row in df[df["entry_time_clean"].isna()].iterrows():
        add_anomaly(
            anomalies,
            row,
            "high",
            "invalid_entry_time",
            "Entry time is missing or could not be parsed.",
            "Fix the entry time format or recover the value from the source system.",
        )

    # 5. Exit before entry
    exit_before_entry = (
        df["entry_time_clean"].notna()
        & df["exit_time_clean"].notna()
        & (df["exit_time_clean"] < df["entry_time_clean"])
    )

    for _, row in df[exit_before_entry].iterrows():
        add_anomaly(
            anomalies,
            row,
            "high",
            "exit_before_entry",
            "Exit time is earlier than entry time.",
            "Check whether entry and exit times were swapped or incorrectly recorded.",
        )

    # 6. Negative amount
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

    # 7. Successful payment with zero or missing amount
    successful_zero_amount = (
        df["payment_status_clean"].str.contains("succeeded", na=False)
        & (df["amount_clean"].isna() | (df["amount_clean"] <= 0))
    )

    for _, row in df[successful_zero_amount].iterrows():
        add_anomaly(
            anomalies,
            row,
            "medium",
            "successful_payment_zero_amount",
            "Payment status says succeeded, but amount is missing or zero.",
            "Check whether this was a validation ticket, free parking, or a payment export issue.",
        )

    # 8. Weird duration values
    if duration_col:
        weird_duration = (
            df[duration_col].notna()
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

    # 9. Suspiciously long duration
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

    # 10. Same location, same hour, same duration, different prices
    price_check = df[
        df["location"].notna()
        & df["entry_time_clean"].notna()
        & df["duration_hours"].notna()
        & df["amount_clean"].notna()
        & (~df["is_extension_ticket"])
    ].copy()

    price_check["entry_hour"] = price_check["entry_time_clean"].dt.floor("h")

    grouped = price_check.groupby(["location", "entry_hour", "duration_hours"])["amount_clean"].nunique()
    bad_groups = grouped[grouped > 1].reset_index()

    for _, bad_group in bad_groups.iterrows():
        matches = price_check[
            (price_check["location"] == bad_group["location"])
            & (price_check["entry_hour"] == bad_group["entry_hour"])
            & (price_check["duration_hours"] == bad_group["duration_hours"])
        ]

        for _, row in matches.iterrows():
            add_anomaly(
                anomalies,
                row,
                "high",
                "same_time_duration_different_prices",
                (
                    f"Same location, same entry hour, and same duration have multiple prices. "
                    f"Location: {row['location']}, hour: {row['entry_hour']}, duration: {row['duration_hours']}."
                ),
                "Check whether the pricing rules changed mid-hour or whether one of the prices is incorrect.",
            )

    # 11. Dead periods of 4+ hours with no entries
    entry_rows = df[df["entry_time_clean"].notna() & df["location"].notna()].copy()
    entry_rows = entry_rows.sort_values(["location", "entry_time_clean"])

    for location, group in entry_rows.groupby("location"):
        group = group.sort_values("entry_time_clean")
        time_diffs = group["entry_time_clean"].diff()

        dead_periods = group[time_diffs > pd.Timedelta(hours=4)]

        for idx, row in dead_periods.iterrows():
            previous_time = group.loc[group.index[group.index.get_loc(idx) - 1], "entry_time_clean"]

            add_anomaly(
                anomalies,
                row,
                "low",
                "dead_period_over_4_hours",
                (
                    f"No recorded entries at {location} for more than 4 hours. "
                    f"Previous entry: {previous_time}. Next entry: {row['entry_time_clean']}."
                ),
                "Check whether this was truly a dead period or whether data is missing.",
            )

    anomaly_columns = [
        "row_number",
        "location",
        "ticket_id",
        "severity",
        "anomaly_type",
        "description",
        "suggested_fix",
    ]
    anomaly_df = pd.DataFrame(anomalies, columns=anomaly_columns)

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

    