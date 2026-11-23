import csv
import pandas as pd
import pyModeS as pms

INPUT  = "/media/pi/SANDISK/A939/dump_20260612_140347_4B7FB1_raw.csv"
OUTPUT = "/media/pi/SANDISK/A939/dump_20260612_140347_4B7FB1.csv"

raw_df = pd.read_csv(INPUT, encoding="latin-1", engine="python", on_bad_lines="skip")

with open(OUTPUT, "w", newline="", encoding="utf-8") as fout:
    writer = csv.writer(fout, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["timestamp (UTC)", "icao", "field", "value"])

    for _, row in raw_df.iterrows():
        ts   = row["timestamp (UTC)"]
        icao = row["icao"]
        raw  = str(row["raw_msg"]).strip()

        try:
            decoded = pms.decode(raw)
        except Exception as e:
            writer.writerow([ts, icao, "error", str(e)])
            writer.writerow([ts, icao, "raw_msg", raw])
            continue

        for field, value in decoded.items():
            if field == "icao":
                continue  # already in the icao column
            writer.writerow([ts, icao, field, value])

        writer.writerow([ts, icao, "raw_msg", raw])

print(f"Done -> {OUTPUT}")
