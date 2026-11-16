#!/usr/bin/env python3
"""Archive NSE's daily pre-open snapshot for research.

This stores the NSE pre-open API response as received, plus a flat CSV of
per-security metadata. It is a best-effort collector, not an official NSE
historical archive or a guaranteed real-time service.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from curl_cffi.requests import Session

IST = ZoneInfo("Asia/Kolkata")
PAGE_URL = "https://www.nseindia.com/market-data/pre-open-market-cm-and-emerge-market"
API_URL = "https://www.nseindia.com/api/market-data-pre-open?key=ALL"
ROOT = Path("data/preopen")


def fetch_snapshot(retries: int = 3) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with Session(impersonate="chrome", timeout=25) as session:
                headers = {
                    "Accept": "application/json, text/plain, */*",
                    "Referer": PAGE_URL,
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                }
                session.get(PAGE_URL, headers=headers)
                response = session.get(API_URL, headers=headers)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise ValueError("NSE response did not contain a data array")
                if not payload["data"]:
                    raise ValueError("NSE returned an empty pre-open data array")
                return payload
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Could not retrieve NSE pre-open data: {last_error}") from last_error


def metadata_rows(payload: dict) -> list[dict]:
    rows = []
    for item in payload.get("data", []):
        if isinstance(item, dict):
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                rows.append(metadata)
    return rows


def main() -> int:
    payload = fetch_snapshot()
    now = datetime.now(IST)
    day_dir = ROOT / now.strftime("%Y") / now.strftime("%m")
    day_dir.mkdir(parents=True, exist_ok=True)
    stem = now.strftime("%Y-%m-%d")
    rows = metadata_rows(payload)
    if not rows:
        raise RuntimeError("NSE response had no per-security metadata rows")

    envelope = {
        "source": "NSE India pre-open market API",
        "source_url": API_URL,
        "retrieved_at_ist": now.isoformat(),
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "record_count": len(rows),
        "data": payload,
    }

    json_path = day_dir / f"{stem}.json"
    csv_path = day_dir / f"{stem}.csv"
    json_path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    columns = sorted({key for row in rows for key in row.keys()})
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved {len(rows)} securities to {json_path} and {csv_path}")
    print(f"Snapshot time (IST): {now.isoformat()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Pre-open archive failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
