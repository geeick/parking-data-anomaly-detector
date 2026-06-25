import os
import tempfile

import pandas as pd
import streamlit as st

from src.detect_anomalies import detect_anomalies


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

if uploaded_file is not None:
    st.success(f"Uploaded: {uploaded_file.name}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as temp_file:
        temp_file.write(uploaded_file.getvalue())
        temp_path = temp_file.name

    try:
        anomaly_df, original_columns = detect_anomalies(temp_path)

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
        else:
            st.subheader("Anomalies by Type")

            if "anomaly_type" in anomaly_df.columns:
                type_counts = anomaly_df["anomaly_type"].value_counts().reset_index()
                type_counts.columns = ["anomaly_type", "count"]

                st.dataframe(type_counts, use_container_width=True)
                st.bar_chart(type_counts.set_index("anomaly_type"))

            st.subheader("Full Anomaly Report")

            severity_filter = st.multiselect(
                "Filter by severity",
                options=sorted(anomaly_df["severity"].dropna().unique()),
                default=sorted(anomaly_df["severity"].dropna().unique()),
            )

            type_filter = st.multiselect(
                "Filter by anomaly type",
                options=sorted(anomaly_df["anomaly_type"].dropna().unique()),
                default=sorted(anomaly_df["anomaly_type"].dropna().unique()),
            )

            filtered_df = anomaly_df[
                anomaly_df["severity"].isin(severity_filter)
                & anomaly_df["anomaly_type"].isin(type_filter)
            ]

            st.dataframe(filtered_df, use_container_width=True)

            csv_bytes = filtered_df.to_csv(index=False).encode("utf-8")

            st.download_button(
                label="Download filtered anomaly report",
                data=csv_bytes,
                file_name="anomaly_report.csv",
                mime="text/csv",
            )

    except Exception as error:
        st.error("The report could not be analyzed.")
        st.exception(error)

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

else:
    st.info("Upload a report to begin.")