from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

from .sec_client import SEC_WWW, SecClient

FORM_TYPES = {"13F-HR", "13F-HR/A"}
MASTER_URL = "{base}/Archives/edgar/full-index/{year}/QTR{quarter}/master.idx"
ACCEPTANCE_RE = re.compile(r"<ACCEPTANCE-DATETIME>(\d{14})")


def parse_master_index(text: str) -> pd.DataFrame:
    rows: list[dict] = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik, company_name, form_type, filed_date, filename = [p.strip() for p in parts]
        if form_type not in FORM_TYPES:
            continue
        accession_txt = Path(filename).name
        accession_no = accession_txt.removesuffix(".txt")
        accession_nodash = accession_no.replace("-", "")
        rows.append({
            "manager_cik": str(cik).zfill(10),
            "manager_name": company_name,
            "form_type": form_type,
            "filed_date": filed_date,
            "archive_filename": filename,
            "accession_number": accession_no,
            "accession_nodash": accession_nodash,
        })
    return pd.DataFrame(rows)


def acceptance_datetime_from_submission_text(text: str) -> str | None:
    match = ACCEPTANCE_RE.search(text)
    if not match:
        return None
    value = match.group(1)
    return pd.Timestamp.strptime(value, "%Y%m%d%H%M%S").isoformat()


def filing_text_url(manager_cik: str, accession_nodash: str, archive_filename: str) -> str:
    cik_int = str(int(manager_cik))
    name = Path(archive_filename).name
    return f"{SEC_WWW}/Archives/edgar/data/{cik_int}/{accession_nodash}/{name}"


def collect_index(year: int, quarter: int, output_dir: Path, hydrate_sample: int = 10) -> dict:
    if quarter not in {1, 2, 3, 4}:
        raise ValueError("quarter must be 1..4")
    output_dir.mkdir(parents=True, exist_ok=True)
    client = SecClient()
    url = MASTER_URL.format(base=SEC_WWW, year=year, quarter=quarter)
    text = client.get_text(url)
    filings = parse_master_index(text)
    if filings.empty:
        raise RuntimeError(f"No 13F-HR/13F-HR/A filings found in {url}")

    filings["accepted_at"] = pd.NA
    filings["acceptance_state"] = "NOT_HYDRATED"
    sample_n = min(max(int(hydrate_sample), 0), len(filings))
    for idx in filings.index[:sample_n]:
        row = filings.loc[idx]
        filing_url = filing_text_url(
            str(row["manager_cik"]),
            str(row["accession_nodash"]),
            str(row["archive_filename"]),
        )
        try:
            submission_text = client.get_text(filing_url)
            accepted = acceptance_datetime_from_submission_text(submission_text)
            if accepted:
                filings.at[idx, "accepted_at"] = accepted
                filings.at[idx, "acceptance_state"] = "HYDRATED"
            else:
                filings.at[idx, "acceptance_state"] = "MISSING_IN_HEADER"
        except Exception as exc:
            filings.at[idx, "acceptance_state"] = f"ERROR:{type(exc).__name__}"

    filings.to_csv(output_dir / "filings.csv", index=False)
    hydrated = filings["acceptance_state"].eq("HYDRATED")
    summary = {
        "source_url": url,
        "year": int(year),
        "quarter": int(quarter),
        "filing_count": int(len(filings)),
        "base_13f_hr_count": int(filings["form_type"].eq("13F-HR").sum()),
        "amendment_13f_hra_count": int(filings["form_type"].eq("13F-HR/A").sum()),
        "unique_manager_count": int(filings["manager_cik"].nunique()),
        "acceptance_sample_requested": int(sample_n),
        "acceptance_sample_hydrated": int(hydrated.sum()),
        "acceptance_sample_failed": int(sample_n - hydrated.sum()),
        "strategy_returns_inspected": False,
        "pit_attach_ready": False,
        "note": "Index discovery only. Strategy attachment remains blocked until all relevant accessions have accepted_at and amendment lineage audited.",
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--quarter", type=int, default=2)
    parser.add_argument("--hydrate-sample", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, default=Path("data/13f/index_probe"))
    args = parser.parse_args()
    summary = collect_index(args.year, args.quarter, args.output_dir, args.hydrate_sample)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
