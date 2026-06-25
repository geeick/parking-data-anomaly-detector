import os
import tempfile

import pandas as pd
import streamlit as st

from src.detect_anomalies import detect_anomalies, read_parking_report


ANOMALY_DISPLAY_COLUMNS = [
    "row_number",
    "related_row_numbers",
    "severity",
    "anomaly_type",
    "location",
    "ticket_id",
    "description",
    "suggested_fix",
]


def parse_related_row_numbers(value):
    """Convert a value like '80, 81' into [80, 81]."""
    if pd.isna(value):
        return []

    row_numbers = []

    for piece in str(value).split(","):
        piece = piece.strip()

        if piece == "":
            continue

        try:
            row_numbers.append(int(float(piece)))
        except ValueError:
            pass

    return row_numbers


def prepare_original_report_rows(original_df):
    """Add a row_number column that matches the anomaly report."""
    original_df = original_df.copy()

    if "_source_line_number" in original_df.columns:
        original_df["row_number"] = original_df["_source_line_number"]
    else:
        original_df["row_number"] = range(3, len(original_df) + 3)

    return original_df


def show_original_rows_for_selected_anomaly(selected_anomaly, original_df):
    """Show the original report rows related to the selected anomaly."""
    related_row_numbers = parse_related_row_numbers(
        selected_anomaly.get("related_row_numbers", selected_anomaly["row_number"])
    )

    if not related_row_numbers:
        related_row_numbers = [selected_anomaly["row_number"]]

    st.subheader("Original Report Row(s)")

    if len(related_row_numbers) == 1:
        st.write(f"Showing original report row: **{related_row_numbers[0]}**")
    else:
        st.write(
            "Showing original report rows involved in this anomaly: "
            + ", ".join(str(row_number) for row_number in related_row_numbers)
        )

    original_match = original_df[
        original_df["row_number"].isin(related_row_numbers)
    ].copy()

    if original_match.empty:
        st.warning("Could not find the matching original report rows.")
        return

    original_match["_sort_order"] = original_match["row_number"].apply(
        lambda row_number: related_row_numbers.index(row_number)
        if row_number in related_row_numbers
        else 999999
    )
    original_match = original_match.sort_values("_sort_order")

    st.dataframe(
        original_match.drop(
            columns=["_source_line_number", "_sort_order"],
            errors="ignore",
        ),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("View row details", expanded=True):
        for _, original_row in original_match.iterrows():
            st.markdown(f"### Report row {original_row['row_number']}")

            row_dict = original_row.drop(
                labels=["_source_line_number", "_sort_order"],
                errors="ignore",
            ).to_dict()

            for key, value in row_dict.items():
                st.write(f"**{key}:** {value}")

            st.divider()


def show_anomaly_report(anomaly_df, original_df):
    """Show filters, selectable anomaly table, original rows, and download button."""
    st.subheader("Full Anomaly Report")

    severity_options = sorted(anomaly_df["severity"].dropna().unique())
    type_options = sorted(anomaly_df["anomaly_type"].dropna().unique())

    severity_filter = st.multiselect(
        "Filter by severity",
        options=severity_options,
        default=severity_options,
    )

    type_filter = st.multiselect(
        "Filter by anomaly type",
        options=type_options,
        default=type_options,
    )

    filtered_df = anomaly_df[
        anomaly_df["severity"].isin(severity_filter)
        & anomaly_df["anomaly_type"].isin(type_filter)
    ].copy()

    if filtered_df.empty:
        st.info("No anomalies match the current filters.")
        return

    display_columns = [
        col for col in ANOMALY_DISPLAY_COLUMNS
        if col in filtered_df.columns
    ]

    st.write(
        "Click an anomaly row below to see the original report row or rows involved."
    )

    selection = st.dataframe(
        filtered_df[display_columns],
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="anomaly_table",
    )

    selected_rows = selection.selection.rows

    if selected_rows:
        selected_position = selected_rows[0]
        selected_anomaly = filtered_df.iloc[selected_position]
        show_original_rows_for_selected_anomaly(selected_anomaly, original_df)
    else:
        st.info("Select an anomaly row to see the original report row or rows.")

    csv_bytes = filtered_df.to_csv(index=False).encode("utf-8")

    st.download_button(
        label="Download filtered anomaly report",
        data=csv_bytes,
        file_name="anomaly_report.csv",
        mime="text/csv",
    )


def main():
    st.set_page_config(
        page_title="Parking Data Anomaly Detector",
        page_icon="🚗",
        layout="wide",
    )

    st.title("🚗 Parking Data Anomaly Detector")

    st.write(
        "Upload a SafetyPark-style parking report, and this tool will scan it for "
        "pricing, payment, timing, and data-quality anomalies."
    )

    uploaded_file = st.file_uploader(
        "Upload parking report",
        type=["csv", "txt", "tsv"],
    )

    if uploaded_file is None:
        st.info("Upload a report to begin.")
        return

    st.success(f"Uploaded: {uploaded_file.name}")

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as temp_file:
            temp_file.write(uploaded_file.getvalue())
            temp_path = temp_file.name

        anomaly_df, original_columns = detect_anomalies(temp_path)
        original_df = prepare_original_report_rows(read_parking_report(temp_path))

        st.subheader("Report Summary")

        total_anomalies = len(anomaly_df)

        high_count = 0
        medium_count = 0
        low_count = 0

        if total_anomalies > 0 and "severity" in anomaly_df.columns:
            severity_counts = anomaly_df["severity"].value_counts()
            high_count = int(severity_counts.get("high", 0))
            medium_count = int(severity_counts.get("medium", 0))
            low_count = int(severity_counts.get("low", 0))

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total anomalies", total_anomalies)
        col2.metric("High severity", high_count)
        col3.metric("Medium severity", medium_count)
        col4.metric("Low severity", low_count)

        st.subheader("Columns Found")
        st.write(", ".join(original_columns))

        if total_anomalies == 0:
            st.success("No anomalies found.")
            return

        st.subheader("Anomalies by Type")

        if "anomaly_type" in anomaly_df.columns:
            type_counts = anomaly_df["anomaly_type"].value_counts().reset_index()
            type_counts.columns = ["anomaly_type", "count"]

            st.dataframe(type_counts, use_container_width=True, hide_index=True)
            st.bar_chart(type_counts.set_index("anomaly_type"))

        show_anomaly_report(anomaly_df, original_df)

    except Exception as error:
        st.error("The report could not be analyzed.")
        st.exception(error)

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


if __name__ == "__main__":
    main()
