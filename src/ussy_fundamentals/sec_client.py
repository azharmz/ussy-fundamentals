from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"
REQUEST_DELAY = 0.15


class SecClient:
    def __init__(self, user_agent: str | None = None):
        self.user_agent = user_agent or os.getenv("SEC_USER_AGENT")
        if not self.user_agent:
            raise RuntimeError("Set SEC_USER_AGENT, e.g. 'USSY Research your@email.com'")
        self.session = requests.Session()
        self.last_request = 0.0

    def get_json(self, url: str) -> dict[str, Any]:
        elapsed = time.time() - self.last_request
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        r = self.session.get(
            url,
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=60,
        )
        self.last_request = time.time()
        r.raise_for_status()
        return r.json()


def save_json_immutable(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def columnar_to_rows(data: dict) -> list[dict]:
    if not data:
        return []
    keys = list(data)
    n = len(data[keys[0]])
    return [
        {k: (data.get(k, [])[i] if i < len(data.get(k, [])) else None) for k in keys}
        for i in range(n)
    ]


def ticker_mapping(client: SecClient, cache_dir: Path) -> pd.DataFrame:
    path = cache_dir / "company_tickers.json"
    if not path.exists():
        save_json_immutable(path, client.get_json(f"{SEC_WWW}/files/company_tickers.json"))
    payload = load_json(path)
    return pd.DataFrame([
        {"symbol": str(v["ticker"]).upper(), "cik": str(v["cik_str"]).zfill(10), "company_name": v["title"]}
        for v in payload.values()
    ])


def submissions(client: SecClient, cik: str, cache_dir: Path) -> list[dict]:
    main_path = cache_dir / f"CIK{cik}.json"
    if not main_path.exists():
        save_json_immutable(main_path, client.get_json(f"{SEC_DATA}/submissions/CIK{cik}.json"))
    main = load_json(main_path)
    rows = columnar_to_rows(main.get("filings", {}).get("recent", {}))
    for meta in main.get("filings", {}).get("files", []):
        name = meta.get("name")
        if not name:
            continue
        path = cache_dir / cik / name
        if not path.exists():
            save_json_immutable(path, client.get_json(f"{SEC_DATA}/submissions/{name}"))
        rows.extend(columnar_to_rows(load_json(path)))
    return rows


def companyfacts(client: SecClient, cik: str, cache_dir: Path) -> dict:
    path = cache_dir / f"CIK{cik}.json"
    if not path.exists():
        try:
            payload = client.get_json(f"{SEC_DATA}/api/xbrl/companyfacts/CIK{cik}.json")
        except requests.HTTPError as exc:
            response = exc.response
            if response is not None and response.status_code == 404:
                print(f"WARN CIK={cik} SEC companyfacts not found (404); continuing without facts")
                return {}
            raise
        save_json_immutable(path, payload)
    return load_json(path)
