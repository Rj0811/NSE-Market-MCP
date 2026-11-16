#!/usr/bin/env python3
"""Historical NSE cash + F&O scanner backtest (daily, end-of-day data only).

Downloads official NSE archives, builds a point-in-time bullish score from:
- cash price/volume/delivery
- nearest-expiry stock futures price and OI
- nearest-expiry near-ATM stock option OI
Then evaluates the top five names against the next session.

This is a research prototype, not trading advice. It does not use historical
intraday option-chain snapshots or pre-open snapshots.
"""
from __future__ import annotations

import argparse
import io
import json
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from curl_cffi import requests

BASE = "https://nsearchives.nseindia.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/csv,application/zip,*/*",
    "Referer": "https://www.nseindia.com/",
}
IST = ZoneInfo("Asia/Kolkata")
RAW = Path("backtest-data/raw")
OUT = Path("backtest-output")


def fetch_bytes(session, url: str) -> bytes | None:
    for attempt in range(3):
        try:
            response = session.get(url, headers=HEADERS, timeout=35)
            if response.status_code == 200 and len(response.content) > 100:
                return response.content
        except Exception as exc:
            print(f"download retry {attempt + 1}: {exc}")
        time.sleep(1.5 * (attempt + 1))
    return None


def read_zip_csv(blob: bytes) -> pd.DataFrame | None:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if not names:
                return None
            with archive.open(names[0]) as handle:
                return pd.read_csv(handle, low_memory=False)
    except Exception:
        return None


def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip().replace("\ufeff", "") for c in df.columns]
    return df


def download_day(session, day: date) -> dict | None:
    key = day.strftime("%Y%m%d")
    folder = RAW / key
    folder.mkdir(parents=True, exist_ok=True)

    cash_path = folder / "cash_delivery.csv"
    fo_path = folder / "fo.csv"

    if cash_path.exists() and fo_path.exists():
        cash = pd.read_csv(cash_path, low_memory=False)
        fo = pd.read_csv(fo_path, low_memory=False)
        return {"date": day.isoformat(), "cash": clean_columns(cash), "fo": clean_columns(fo)}

    cash_url = f"{BASE}/products/content/sec_bhavdata_full_{day.strftime('%d%m%Y')}.csv"
    fo_url = f"{BASE}/content/fo/BhavCopy_NSE_FO_0_0_0_{key}_F_0000.csv.zip"

    cash_blob = fetch_bytes(session, cash_url)
    fo_blob = fetch_bytes(session, fo_url)
    if not cash_blob or not fo_blob:
        return None

    try:
        cash = pd.read_csv(io.BytesIO(cash_blob), low_memory=False)
        fo = read_zip_csv(fo_blob)
        if fo is None:
            return None
        cash = clean_columns(cash)
        fo = clean_columns(fo)
        cash.to_csv(cash_path, index=False)
        fo.to_csv(fo_path, index=False)
        return {"date": day.isoformat(), "cash": cash, "fo": fo}
    except Exception as exc:
        print(f"{day}: parse failed: {exc}")
        return None


def col(df: pd.DataFrame, name: str, default=None):
    if name in df.columns:
        return df[name]
    return pd.Series(default, index=df.index)


def numeric(df: pd.DataFrame, name: str) -> pd.Series:
    return pd.to_numeric(col(df, name), errors="coerce")


def normalize_cash(frame: dict) -> pd.DataFrame:
    df = frame["cash"].copy()
    required = ["SYMBOL", "SERIES", "CLOSE_PRICE", "OPEN_PRICE", "HIGH_PRICE",
                "LOW_PRICE", "TTL_TRD_QNTY", "DELIV_PER"]
    if not set(required).issubset(df.columns):
        raise ValueError(f"Cash file missing columns: {sorted(set(required)-set(df.columns))}")
    df["symbol"] = df["SYMBOL"].astype(str).str.strip().str.upper()
    df["series"] = df["SERIES"].astype(str).str.strip().str.upper()
    df = df[df["series"] == "EQ"].copy()
    for src, dst in [("CLOSE_PRICE","close"),("OPEN_PRICE","open"),
                     ("HIGH_PRICE","high"),("LOW_PRICE","low"),
                     ("TTL_TRD_QNTY","volume"),("DELIV_PER","delivery_pct"),
                     ("PREV_CLOSE","prev_close")]:
        df[dst] = pd.to_numeric(col(df, src), errors="coerce")
    df = df.dropna(subset=["close","open","high","low","volume"])
    df["day_return"] = df["close"] / df["prev_close"] - 1
    spread = (df["high"] - df["low"]).replace(0, pd.NA)
    df["close_location"] = ((df["close"] - df["low"]) / spread).fillna(0.5).clip(0,1)
    return df[["symbol","close","open","high","low","volume","delivery_pct",
               "day_return","close_location"]]


def normalize_fo(frame: dict, trade_day: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = frame["fo"].copy()
    required = ["TckrSymb","FinInstrmNm","XpryDt","ClsPric","OpnIntrst",
                "ChngInOpnIntrst","TtlTradgVol","UndrlygPric","OptnTp","StrkPric"]
    if not set(required).issubset(df.columns):
        raise ValueError(f"F&O file missing columns: {sorted(set(required)-set(df.columns))}")
    df["symbol"] = df["TckrSymb"].astype(str).str.strip().str.upper()
    df["instrument"] = df["FinInstrmNm"].astype(str).str.strip().str.upper()
    df["expiry"] = pd.to_datetime(df["XpryDt"], errors="coerce", dayfirst=True).dt.date
    for src, dst in [("ClsPric","close"),("PrvsClsgPric","prev_close"),
                     ("OpnIntrst","oi"),("ChngInOpnIntrst","oi_change"),
                     ("TtlTradgVol","volume"),("UndrlygPric","underlying"),
                     ("StrkPric","strike")]:
        df[dst] = pd.to_numeric(col(df, src), errors="coerce")
    df = df[(df["expiry"] >= trade_day) & (df["volume"].fillna(0) > 0)].copy()

    fut = df[df["instrument"].eq("FUTSTK")].copy()
    if not fut.empty:
        fut = fut.sort_values(["symbol","expiry","volume"], ascending=[True,True,False])
        fut = fut.drop_duplicates(["symbol"], keep="first")
        fut["fut_return"] = fut["close"] / fut["prev_close"] - 1
        fut["oi_pct_change"] = fut["oi_change"] / (fut["oi"] - fut["oi_change"]).replace(0, pd.NA)
        fut = fut[["symbol","expiry","fut_return","oi","oi_change","oi_pct_change"]]

    opt = df[df["instrument"].eq("OPTSTK")].copy()
    if not opt.empty:
        # For each symbol use the nearest expiry and strikes within +/-5% of spot.
        opt["nearest_expiry"] = opt.groupby("symbol")["expiry"].transform("min")
        opt = opt[opt["expiry"] == opt["nearest_expiry"]].copy()
        opt = opt[opt["underlying"].notna() & (opt["underlying"] > 0)]
        opt = opt[(opt["strike"] >= opt["underlying"] * 0.95) &
                  (opt["strike"] <= opt["underlying"] * 1.05)]
        opt["side"] = opt["OptnTp"].astype(str).str.upper().str.strip()
        opt = opt[opt["side"].isin(["CE","PE"])]
        agg = opt.groupby(["symbol","side"], as_index=False).agg(
            oi=("oi","sum"), oi_change=("oi_change","sum"), volume=("volume","sum"))
        wide = agg.pivot(index="symbol", columns="side", values=["oi","oi_change","volume"])
        wide.columns = ["_".join(x).lower() for x in wide.columns]
        wide = wide.reset_index()
        for name in ["oi_ce","oi_pe","oi_change_ce","oi_change_pe","volume_ce","volume_pe"]:
            if name not in wide:
                wide[name] = 0.0
        wide["pcr"] = wide["oi_pe"] / wide["oi_ce"].replace(0, pd.NA)
        opt = wide[["symbol","oi_ce","oi_pe","oi_change_ce","oi_change_pe",
                    "volume_ce","volume_pe","pcr"]]
    else:
        opt = pd.DataFrame(columns=["symbol","oi_ce","oi_pe","oi_change_ce",
                                    "oi_change_pe","volume_ce","volume_pe","pcr"])
    return fut, opt


def build_scores(frames: list[dict]) -> tuple[pd.DataFrame, list[str]]:
    cash_by_day = {}
    fut_by_day = {}
    opt_by_day = {}
    issues = []
    for frame in frames:
        d = date.fromisoformat(frame["date"])
        cash_by_day[d] = normalize_cash(frame)
        fut_by_day[d], opt_by_day[d] = normalize_fo(frame, d)

    dates = sorted(cash_by_day)
    cash_history = {}
    for d in dates:
        c = cash_by_day[d].copy()
        cash_history[d] = c
    scores = []

    for idx, d in enumerate(dates[:-1]):
        next_day = dates[idx + 1]
        cash = cash_history[d].copy()
        # Calculate trailing averages using only sessions strictly before signal day.
        prior_dates = dates[max(0, idx-5):idx]
        if prior_dates:
            prior = pd.concat([cash_history[p][["symbol","volume","delivery_pct"]]
                               for p in prior_dates], ignore_index=True)
            means = prior.groupby("symbol").agg(
                prior_avg_volume=("volume","mean"),
                prior_avg_delivery=("delivery_pct","mean")).reset_index()
            cash = cash.merge(means, on="symbol", how="left")
        else:
            cash["prior_avg_volume"] = pd.NA
            cash["prior_avg_delivery"] = pd.NA
        cash["volume_ratio"] = cash["volume"] / cash["prior_avg_volume"].replace(0, pd.NA)
        cash["delivery_delta"] = cash["delivery_pct"] - cash["prior_avg_delivery"]

        fut = fut_by_day[d]
        opt = opt_by_day[d]
        nxt = cash_history[next_day][["symbol","open","close"]].rename(
            columns={"open":"next_open","close":"next_close"})
        cash = cash.merge(fut, on="symbol", how="left").merge(opt, on="symbol", how="left")
        cash = cash.merge(nxt, on="symbol", how="inner")
        if cash.empty:
            continue

        # Cash: 35 points
        cash_score = (
            (cash["day_return"] > 0).astype(int) * 12 +
            (cash["close_location"] >= 0.70).astype(int) * 8 +
            (cash["volume_ratio"] >= 1.5).astype(int) * 8 +
            ((cash["delivery_delta"] > 0) & (cash["delivery_pct"] >= 35)).astype(int) * 7
        )
        # Futures: 35 points; positive price + positive OI = long buildup.
        fut_score = (
            (cash["fut_return"] > 0).astype("Int64").fillna(0) * 15 +
            (cash["oi_change"] > 0).astype("Int64").fillna(0) * 10 +
            ((cash["fut_return"] > 0) & (cash["oi_change"] > 0)).astype(int) * 10
        )
        # Options: 30 points; put OI adds / call OI reduces near ATM.
        pchg = cash["oi_change_pe"].fillna(0)
        cchg = cash["oi_change_ce"].fillna(0)
        opt_score = (
            (pchg > 0).astype(int) * 10 +
            (cchg < 0).astype(int) * 10 +
            (cash["pcr"] >= 1.0).astype("Int64").fillna(0) * 10
        )
        cash["score"] = cash_score + fut_score + opt_score
        cash["signal_date"] = d.isoformat()
        cash["outcome_date"] = next_day.isoformat()
        cash["next_close_return"] = cash["next_close"] / cash["close"] - 1
        cash["next_open_close_return"] = cash["next_close"] / cash["next_open"] - 1
        cash["next_day_positive"] = cash["next_close_return"] > 0
        cash["universe_count"] = len(cash)
        scores.append(cash)

    if not scores:
        raise RuntimeError("No signal/outcome pairs could be built from downloaded sessions.")
    result = pd.concat(scores, ignore_index=True)
    return result, issues


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--end-date", default=None, help="Optional YYYY-MM-DD; defaults to today IST")
    parser.add_argument("--calendar-days", type=int, default=75)
    args = parser.parse_args()

    end = date.fromisoformat(args.end_date) if args.end_date else datetime.now(IST).date()
    session = requests.Session(impersonate="chrome")
    frames = []
    cursor = end
    start = end - timedelta(days=args.calendar_days)
    print(f"Looking for {args.sessions + 1} complete cash/F&O sessions ending by {end}...")
    while cursor >= start and len(frames) < args.sessions + 1:
        if cursor.weekday() < 5:
            item = download_day(session, cursor)
            if item:
                frames.append(item)
                print(f"Downloaded {cursor}: cash={len(item['cash'])} rows, F&O={len(item['fo'])} rows")
        cursor -= timedelta(days=1)

    frames.sort(key=lambda x: x["date"])
    if len(frames) < args.sessions + 1:
        raise RuntimeError(
            f"Only {len(frames)} complete sessions found; need {args.sessions + 1}. "
            "NSE archive may be temporarily unavailable or the requested window too short."
        )

    scored, _ = build_scores(frames)
    signal_dates = sorted(scored["signal_date"].unique())[-args.sessions:]
    scored = scored[scored["signal_date"].isin(signal_dates)].copy()
    scored = scored.sort_values(["signal_date","score","volume_ratio"], ascending=[True,False,False])

    top5 = scored.groupby("signal_date", group_keys=False).head(5).copy()
    top10 = scored.groupby("signal_date", group_keys=False).head(10).copy()
    summary = {
        "requested_sessions": args.sessions,
        "tested_signal_sessions": len(signal_dates),
        "first_signal_date": signal_dates[0],
        "last_signal_date": signal_dates[-1],
        "universe": "All symbols with active stock futures and matching cash EQ rows",
        "score_max": 100,
        "modules": {"cash": 35, "futures": 35, "options": 30},
        "top5": {
            "picks": int(len(top5)),
            "next_close_positive_hit_rate_pct": round(float(top5["next_day_positive"].mean()*100),2),
            "avg_next_close_return_pct": round(float(top5["next_close_return"].mean()*100),3),
            "avg_next_open_to_close_pct": round(float(top5["next_open_close_return"].mean()*100),3),
        },
        "top10": {
            "picks": int(len(top10)),
            "next_close_positive_hit_rate_pct": round(float(top10["next_day_positive"].mean()*100),2),
            "avg_next_close_return_pct": round(float(top10["next_close_return"].mean()*100),3),
            "avg_next_open_to_close_pct": round(float(top10["next_open_close_return"].mean()*100),3),
        },
        "all_fno_universe": {
            "observations": int(len(scored)),
            "next_close_positive_hit_rate_pct": round(float(scored["next_day_positive"].mean()*100),2),
            "avg_next_close_return_pct": round(float(scored["next_close_return"].mean()*100),3),
        },
        "limitations": [
            "Daily end-of-day data only; no intraday option-chain snapshots.",
            "No historical pre-open snapshots included.",
            "No transaction costs, slippage, taxes, or execution simulation.",
            "Delivery data is joined by symbol from the NSE full bhavcopy report.",
            "First version uses a fixed heuristic score; weights are not optimized.",
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    top5.to_csv(OUT / "top5_daily_picks.csv", index=False)
    top10.to_csv(OUT / "top10_daily_picks.csv", index=False)
    scored.to_csv(OUT / "all_scored_stocks.csv", index=False)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (OUT / "data_coverage.json").write_text(json.dumps({
        "downloaded_sessions": [x["date"] for x in frames],
        "cash_rows_per_session": {x["date"]: int(len(x["cash"])) for x in frames},
        "fo_rows_per_session": {x["date"]: int(len(x["fo"])) for x in frames},
    }, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
