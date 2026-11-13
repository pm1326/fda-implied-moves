"""
Step 1 of building the history: download every FDA decision from the FDA's own public data.

  - Approvals: the Drugs@FDA database (openFDA bulk download).
  - Rejections: the FDA's published Complete Response Letters (openFDA transparency feed).

Writes data/raw/approvals.json and data/raw/crls.json.
"""
import io
import json
import os
import re
import zipfile
from datetime import datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "raw")
START = "20180101"


def fetch_approvals():
    url = "https://download.open.fda.gov/drug/drugsfda/drug-drugsfda-0001-of-0001.json.zip"
    z = zipfile.ZipFile(io.BytesIO(requests.get(url, timeout=300).content))
    data = json.loads(z.read(z.namelist()[0]))["results"]
    out, every = [], []
    for app in data:
        num = app.get("application_number", "")
        if not (num.startswith("NDA") or num.startswith("BLA")):
            continue  # skip generics
        for s in app.get("submissions", []):
            # every first approval ever, used to count how many products a company already had
            if s.get("submission_type") == "ORIG" and s.get("submission_status") == "AP" and s.get("submission_status_date"):
                every.append({"sponsor": app.get("sponsor_name", ""), "date": s["submission_status_date"]})
            # ORIG + AP = the first approval of this application
            if s.get("submission_type") == "ORIG" and s.get("submission_status") == "AP" and s.get("submission_status_date", "") >= START:
                brands = sorted({p.get("brand_name", "") for p in app.get("products", [])})
                out.append({
                    "application": num,
                    "sponsor": app.get("sponsor_name", ""),
                    "drug": brands[0] if brands else "",
                    "date": datetime.strptime(s["submission_status_date"], "%Y%m%d").strftime("%Y-%m-%d"),
                    "class": s.get("submission_class_code") or "",
                    "priority": s.get("review_priority") or "",
                })
    return out, every


def fetch_crls():
    out, skip = [], 0
    while True:
        r = requests.get("https://api.fda.gov/transparency/crl.json", params={"limit": 100, "skip": skip}, timeout=120).json()
        rows = r.get("results", [])
        for c in rows:
            m = re.match(r"(\d{6})", c.get("file_name", ""))
            try:
                d = datetime.strptime(c.get("letter_date", ""), "%m/%d/%Y").strftime("%Y-%m-%d")
            except ValueError:
                continue
            out.append({
                "application": m.group(1) if m else "",
                "sponsor": c.get("company_name", ""),
                "date": d,
                "later_approved": c.get("approval_status", ""),
                "text": c.get("text") or "",
            })
        skip += 100
        if len(rows) < 100:
            break
    return [c for c in out if c["date"] >= "2018-01-01"]


if __name__ == "__main__":
    os.makedirs(RAW, exist_ok=True)
    (a, every), c = fetch_approvals(), fetch_crls()
    json.dump(a, open(os.path.join(RAW, "approvals.json"), "w"), indent=1)
    json.dump(every, open(os.path.join(RAW, "all_approvals.json"), "w"))
    json.dump(c, open(os.path.join(RAW, "crls.json"), "w"), indent=1)
    print(f"{len(a)} approvals and {len(c)} rejection letters since 2018 saved to data/raw/")
