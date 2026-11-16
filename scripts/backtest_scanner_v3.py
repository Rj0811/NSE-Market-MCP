#!/usr/bin/env python3
"""NSE Scanner V3: point-in-time EOD shortlist with next-session range analysis.

Daily OHLC can measure the next session's maximum favourable/adverse excursion,
but cannot establish whether a target or stop was hit first. Intraday candles
are required for that sequencing.
"""
from __future__ import annotations
import argparse, json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
from curl_cffi import requests
from backtest_scanner import download_day, normalize_cash, normalize_fo

IST = ZoneInfo("Asia/Kolkata")
OUT = Path("backtest-output-v3")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sessions", type=int, default=20)
    p.add_argument("--calendar-days", type=int, default=180)
    p.add_argument("--end-date", default=None)
    args = p.parse_args()
    end = date.fromisoformat(args.end_date) if args.end_date else datetime.now(IST).date()
    sess = requests.Session(impersonate="chrome")
    frames, cursor = [], end
    needed = args.sessions + 21  # 20 full prior sessions + signal sessions + outcome
    start = end - timedelta(days=args.calendar_days)
    print(f"Seeking {needed} complete NSE sessions...")
    while cursor >= start and len(frames) < needed:
        if cursor.weekday() < 5:
            item = download_day(sess, cursor)
            if item:
                frames.append(item)
                print(f"Downloaded {cursor}")
        cursor -= timedelta(days=1)
    frames.sort(key=lambda x: x["date"])
    if len(frames) < needed:
        raise RuntimeError(f"Only {len(frames)} sessions available; need {needed}. Increase calendar-days or retry.")

    cash_days, fut_days, opt_days = {}, {}, {}
    for f in frames:
        d = date.fromisoformat(f["date"])
        cash_days[d] = normalize_cash(f)
        fut_days[d], opt_days[d] = normalize_fo(f, d)

    dates = sorted(cash_days)
    daily = []
    for i in range(20, len(dates)-1):
        d, nd = dates[i], dates[i+1]
        cur = cash_days[d].copy()
        hist = pd.concat([cash_days[x].assign(session=x) for x in dates[i-20:i]], ignore_index=True)
        agg = hist.groupby("symbol").agg(
            avg_vol20=("volume","mean"), avg_deliv20=("delivery_pct","mean"),
            sma20=("close","mean"), prior_high20=("high","max"), prior_low20=("low","min")
        ).reset_index()
        cur = cur.merge(agg, on="symbol", how="left")
        cur["vol_ratio20"] = cur["volume"] / cur["avg_vol20"].replace(0, pd.NA)
        cur["delivery_delta20"] = cur["delivery_pct"] - cur["avg_deliv20"]
        cur["above_sma20"] = cur["close"] > cur["sma20"]
        cur["breakout20"] = cur["close"] > cur["prior_high20"]
        cur["breakdown20"] = cur["close"] < cur["prior_low20"]
        cur["close_strength"] = cur["close_location"]
        fut, opt = fut_days[d], opt_days[d]
        nxt = cash_days[nd][["symbol","open","high","low","close"]].rename(columns={
            "open":"next_open","high":"next_high","low":"next_low","close":"next_close"})
        cur = cur.merge(fut, on="symbol", how="left").merge(opt, on="symbol", how="left")
        cur = cur.merge(nxt, on="symbol", how="inner")
        # Keep point-in-time active stock-futures universe, matching V2 coverage.
        cur = cur[cur["fut_return"].notna()].copy()
        if cur.empty: continue

        # 100-point transparent score. Futures price/OI matrix is directional.
        cur["long_buildup"] = (cur["fut_return"] > 0) & (cur["oi_change"] > 0)
        cur["short_buildup"] = (cur["fut_return"] < 0) & (cur["oi_change"] > 0)
        cur["short_covering"] = (cur["fut_return"] > 0) & (cur["oi_change"] < 0)
        cur["long_unwinding"] = (cur["fut_return"] < 0) & (cur["oi_change"] < 0)
        cur["trend_bull"] = (cur["above_sma20"].fillna(False).astype(int)*15 +
                             cur["breakout20"].fillna(False).astype(int)*10)
        cur["trend_bear"] = ((cur["close"] < cur["sma20"]).fillna(False).astype(int)*15 +
                             cur["breakdown20"].fillna(False).astype(int)*10)
        cur["volume_score"] = (cur["vol_ratio20"] >= 2).astype(int)*20
        cur["delivery_score"] = ((cur["delivery_pct"] >= 50).astype(int)*10 +
                                 ((cur["delivery_delta20"] > 0) & (cur["delivery_pct"] >= 40)).astype(int)*10)
        cur["futures_bull_score"] = (cur["long_buildup"].astype(int)*25 +
                                     cur["short_covering"].astype(int)*12)
        cur["futures_bear_score"] = (cur["short_buildup"].astype(int)*25 +
                                     cur["long_unwinding"].astype(int)*12)
        cur["options_bull_score"] = ((cur["pcr"] >= 1.1).fillna(False).astype(int)*10)
        cur["options_bear_score"] = ((cur["pcr"] <= 0.8).fillna(False).astype(int)*10)
        cur["bull_score"] = (cur["trend_bull"] + cur["volume_score"] + cur["delivery_score"] +
                             cur["futures_bull_score"] + cur["options_bull_score"])
        cur["bear_score"] = (cur["trend_bear"] + cur["volume_score"] + cur["delivery_score"] +
                             cur["futures_bear_score"] + cur["options_bear_score"])
        cur["signal_date"], cur["outcome_date"] = d.isoformat(), nd.isoformat()
        # Open-to-extreme excursion: opportunity range, not a simulated fill.
        cur["bull_mfe_pct"] = (cur["next_high"] / cur["next_open"] - 1)*100
        cur["bull_mae_pct"] = (cur["next_low"] / cur["next_open"] - 1)*100
        cur["bear_mfe_pct"] = (cur["next_open"] / cur["next_low"] - 1)*100
        cur["bear_mae_pct"] = (cur["next_open"] / cur["next_high"] - 1)*100
        cur["overnight_gap_pct"] = (cur["next_open"] / cur["close"] - 1)*100
        cur["next_open_close_pct"] = (cur["next_close"] / cur["next_open"] - 1)*100
        cur["close_to_close_pct"] = (cur["next_close"] / cur["close"] - 1)*100
        for target in (1,2,3):
            cur[f"bull_{target}pct_range_hit"] = cur["bull_mfe_pct"] >= target
            cur[f"bear_{target}pct_range_hit"] = cur["bear_mfe_pct"] >= target
        daily.append(cur)
    if not daily: raise RuntimeError("No valid signal/outcome pairs.")
    allrows = pd.concat(daily, ignore_index=True)
    signal_dates = sorted(allrows.signal_date.unique())[-args.sessions:]
    allrows = allrows[allrows.signal_date.isin(signal_dates)].copy()

    def ranked(side, score_col):
        return allrows.sort_values(["signal_date",score_col,"vol_ratio20"], ascending=[True,False,False]).groupby("signal_date", group_keys=False).head(5).copy()
    bull, bear = ranked("bull","bull_score"), ranked("bear","bear_score")
    def stats(df, side):
        if df.empty: return {"picks":0}
        mfe, mae = (("bull_mfe_pct","bull_mae_pct") if side=="bull" else ("bear_mfe_pct","bear_mae_pct"))
        return {
            "picks":int(len(df)),
            "avg_max_favourable_excursion_pct":round(float(df[mfe].mean()),3),
            "avg_max_adverse_excursion_pct":round(float(df[mae].mean()),3),
            "target_range_hit_rate_pct":{str(t):round(float(df[f"{side}_{t}pct_range_hit"].mean()*100),2) for t in (1,2,3)},
            "avg_overnight_gap_pct":round(float(df.overnight_gap_pct.mean()),3),
            "avg_open_to_close_pct":round(float(df.next_open_close_pct.mean()),3),
            "avg_close_to_close_pct":round(float(df.close_to_close_pct.mean()),3)
        }
    summary = {
        "model":"V3 cash-led 100-point score with futures buildup classification and options context",
        "requested_sessions":args.sessions,"tested_signal_sessions":len(signal_dates),
        "first_signal_date":signal_dates[0],"last_signal_date":signal_dates[-1],
        "universe":"EQ stocks with active stock futures in the signal session",
        "score_weights":{"trend_and_20_session_breakout":25,"volume_vs_prior_20_sessions":20,
                         "delivery_level_and_change":20,"futures_price_oi_matrix":25,"options_PCR_context":10},
        "bullish_top5":stats(bull,"bull"),"bearish_top5":stats(bear,"bear"),
        "definitions":{
            "MFE":"For bullish picks, (next-session high / next-session open - 1); bearish is (open / low - 1).",
            "MAE":"For bullish picks, (next-session low / open - 1); bearish is (open / high - 1).",
            "target_range_hit":"Daily high/low reached the threshold relative to open; NOT proof target occurred before stop.",
            "close_strength":"Close position within daily high-low range.",
            "delivery":"Compared with each stock's own prior 20-session average."
        },
        "limitations":[
            "Daily OHLC cannot reveal the order of intraday high and low; target-before-stop requires intraday candles.",
            "No historical 5-minute candles, historical intraday option-chain snapshots, or point-in-time pre-open archive.",
            "20-session breakout is included; 52-week breakout is omitted because this run fetches only a limited history window.",
            "Options PCR is contextual, not proof of support/resistance or buyer/seller intent.",
            "No brokerage, taxes, slippage, liquidity impact, or execution simulation.",
            "Rollover and futures basis are not scored in this iteration; they need validated matched-expiry history."
        ]
    }
    OUT.mkdir(parents=True,exist_ok=True)
    bull.to_csv(OUT/"top5_bullish_v3.csv",index=False)
    bear.to_csv(OUT/"top5_bearish_v3.csv",index=False)
    allrows.to_csv(OUT/"all_scored_stocks_v3.csv",index=False)
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    (OUT/"data_coverage.json").write_text(json.dumps({"sessions":[x.isoformat() for x in dates],
        "cash_rows":{x.isoformat():len(cash_days[x]) for x in dates},
        "futures_rows":{x.isoformat():len(fut_days[x]) for x in dates}},indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))

if __name__=="__main__": main()
