# parking-data-anomaly-detector
A tool that finds weird or suspicious parking records.

Given a parking CSV, it should find things like:

Duplicate ticket numbers
Missing entry times, locations, or amounts
Exit time before entry time
Successful payments with missing or zero amount
Same location, same hour, same duration, but different prices
Very long open tickets
Dead periods where no cars enter for 4+ hours
Weird duration values like blank, text, or impossible numbers


# Running the script
'
python src/detect_anomalies.py --input data/[NAME_OF_REPORT].csv --output reports/anomaly_report.csv
'

# Creating python virtual environment:
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt

# csv format
