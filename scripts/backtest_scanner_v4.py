#!/usr/bin/env python3
"""V4 intraday simulation over V3 top-five daily candidates.

Intraday candles are retrieved from Yahoo Finance chart data (5-minute interval).
This is an unofficial third-party data source; missing/throttled symbols are
reported, never silently treated as losing or winning trades.
"""
from __future__ import annotations
import argparse, json, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
from curl_cffi import requests

IST = ZoneInfo("Asia/Kolkata")
OUT = Path("backtest-output-v4")

def fetch_symbol(symbol: str, days: int):
    ticker = f"{symbol}.NS"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"range": f"{days}d", "interval": "5m", "includePrePost": "false"}
    try:
        s = requests.Session(impersonate="chrome")
        r = s.get(url, params=params, timeout=25,
                  headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"})
        if r.status_code != 200:
            return symbol, None, f"HTTP {r.status_code}"
        payload = r.json()
        result = payload.get("chart", {}).get("result")
        if not result:
            err = payload.get("chart", {}).get("error")
            return symbol, None, str(err or "No chart data")
        obj = result[0]
        ts = obj.get("timestamp") or []
        q = obj.get("indicators", {}).get("quote", [{}])[0]
        if not ts:
            return symbol, None, "No timestamps"
        df = pd.DataFrame({
            "timestamp": pd.to_datetime(ts, unit="s", utc=True).tz_convert(IST),
            "open": q.get("open"), "high": q.get("high"),
            "low": q.get("low"), "close": q.get("close"),
            "volume": q.get("volume")
        }).dropna(subset=["open","high","low","close","volume"])
        df["session_date"] = df["timestamp"].dt.date.astype(str)
        return symbol, df, None
    except Exception as e:
        return symbol, None, str(e)[:200]

def simulate_one(row, bars, side, stop_pct):
    bars = bars.sort_values("timestamp").reset_index(drop=True).copy()
    if len(bars) < 5:
        return None
    typical = (bars.high + bars.low + bars.close) / 3
    vol = bars.volume.clip(lower=0)
    bars["vwap"] = (typical * vol).cumsum() / vol.cumsum().replace(0, pd.NA)
    # First 15 minutes are used for context; entry signal is evaluated after 09:30.
    times = bars.timestamp.dt.strftime("%H:%M")
    signal_idx = None
    for i in range(3, len(bars)-1):
        if times.iloc[i] < "09:30" or times.iloc[i] > "14:55":
            continue
        prev, cur = bars.iloc[i-1], bars.iloc[i]
        if side == "bull" and cur.close > cur.vwap and cur.close > prev.high:
            signal_idx = i
            break
        if side == "bear" and cur.close < cur.vwap and cur.close < prev.low:
            signal_idx = i
            break
    if signal_idx is None:
        return {"trade":False, "reason":"No VWAP + previous-candle breakout entry"}
    entry_bar = bars.iloc[signal_idx+1]
    entry = float(entry_bar.open)
    if entry <= 0:
        return None
    target = entry * (1 + 0.01) if side == "bull" else entry * (1 - 0.01)
    stop = entry * (1 - stop_pct/100) if side == "bull" else entry * (1 + stop_pct/100)
    exit_price, exit_reason, exit_time = None, None, None
    for j in range(signal_idx+1, len(bars)):
        b = bars.iloc[j]
        o,h,l,c = map(float, (b.open,b.high,b.low,b.close))
        if side == "bull":
            if o <= stop:
                exit_price, exit_reason = o, "stop_gap"
            elif o >= target:
                exit_price, exit_reason = target, "target_gap"
            elif l <= stop and h >= target:
                exit_price, exit_reason = stop, "stop_and_target_same_bar_stop_first"
            elif l <= stop:
                exit_price, exit_reason = stop, "stop_loss"
            elif h >= target:
                exit_price, exit_reason = target, "target"
        else:
            if o >= stop:
                exit_price, exit_reason = o, "stop_gap"
            elif o <= target:
                exit_price, exit_reason = target, "target_gap"
            elif h >= stop and l <= target:
                exit_price, exit_reason = stop, "stop_and_target_same_bar_stop_first"
            elif h >= stop:
                exit_price, exit_reason = stop, "stop_loss"
            elif l <= target:
                exit_price, exit_reason = target, "target"
        if exit_reason:
            exit_time = b.timestamp.isoformat()
            break
    if exit_price is None:
        last = bars.iloc[-1]
        exit_price, exit_reason, exit_time = float(last.close), "market_close", last.timestamp.isoformat()
    gross = ((exit_price/entry)-1)*100 if side == "bull" else ((entry/exit_price)-1)*100
    return {
        "trade":True, "side":side, "entry_time":entry_bar.timestamp.isoformat(),
        "entry_price":round(entry,4), "target_price":round(target,4),
        "stop_price":round(stop,4), "exit_time":exit_time,
        "exit_price":round(exit_price,4), "exit_reason":exit_reason,
        "gross_return_pct":round(gross,4),
        "target_hit":exit_reason.startswith("target"),
        "stop_hit":exit_reason.startswith("stop"),
        "signal_date":row["signal_date"], "outcome_date":row["outcome_date"],
        "symbol":row["symbol"], "scanner_score":float(row["bull_score"] if side=="bull" else row["bear_score"])
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--intraday-days",type=int,default=60)
    p.add_argument("--stop-loss-pct",type=float,default=0.5)
    args=p.parse_args()
    root=Path("backtest-output-v3")
    bull_path=root/"top5_bullish_v3.csv"
    bear_path=root/"top5_bearish_v3.csv"
    if not bull_path.exists() or not bear_path.exists():
        raise RuntimeError("V3 output CSVs missing. Run Scanner V3 before V4.")
    bull=pd.read_csv(bull_path).drop_duplicates(["signal_date","outcome_date","symbol"])
    bear=pd.read_csv(bear_path).drop_duplicates(["signal_date","outcome_date","symbol"])
    bull["side"]="bull"; bear["side"]="bear"
    picks=pd.concat([bull,bear],ignore_index=True)
    symbols=sorted(picks.symbol.astype(str).str.upper().unique())
    print(f"Fetching 5-minute history for {len(symbols)} unique symbols...")
    histories, errors={},{}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(fetch_symbol,s,args.intraday_days):s for s in symbols}
        for f in as_completed(futures):
            sym,df,err=f.result()
            if df is not None and not df.empty: histories[sym]=df
            else: errors[sym]=err or "empty"
            print(f"{sym}: {'OK' if df is not None and not df.empty else 'MISSING'}")
    trades, no_entry, no_bars=[],0,0
    for _,r in picks.iterrows():
        sym=str(r.symbol).upper()
        hist=histories.get(sym)
        if hist is None:
            no_bars+=1; continue
        day=hist[hist.session_date==str(r.outcome_date)]
        if day.empty:
            no_bars+=1; continue
        result=simulate_one(r,day,str(r.side),args.stop_loss_pct)
        if result is None:
            no_bars+=1
        elif not result["trade"]:
            no_entry+=1
        else:
            result["source"]="Yahoo Finance chart API, 5-minute candles"
            trades.append(result)
    df=pd.DataFrame(trades)
    OUT.mkdir(parents=True,exist_ok=True)
    if not df.empty: df.to_csv(OUT/"intraday_trades_v4.csv",index=False)
    def metrics(x):
        if x.empty: return {"trades":0}
        return {
            "trades":int(len(x)),
            "target_hit_rate_pct":round(float(x.target_hit.mean()*100),2),
            "stop_hit_rate_pct":round(float(x.stop_hit.mean()*100),2),
            "win_rate_pct":round(float((x.gross_return_pct>0).mean()*100),2),
            "avg_gross_return_pct":round(float(x.gross_return_pct.mean()),3),
            "median_gross_return_pct":round(float(x.gross_return_pct.median()),3),
            "sum_gross_trade_returns_pct":round(float(x.gross_return_pct.sum()),3),
            "exit_reasons":{str(k):int(v) for k,v in x.exit_reason.value_counts().items()}
        }
    summary={
        "model":"V4: V3 top-five daily picks; 5-minute VWAP and previous-candle breakout entry",
        "target_pct":1.0,"stop_loss_pct":args.stop_loss_pct,"reward_to_risk":round(1/args.stop_loss_pct,2),
        "entry_rule":"After 09:30 IST, first 5-minute close above/below cumulative VWAP and above previous candle high/below previous candle low; enter next candle open.",
        "exit_rule":"1% target, configured stop, or last available intraday candle close.",
        "same_candle_rule":"If target and stop both touch in one candle, stop is assumed first.",
        "universe_picks":int(len(picks)),"unique_symbols_requested":len(symbols),
        "symbols_with_intraday_data":len(histories),"symbols_missing_data":errors,
        "picks_without_intraday_bars":no_bars,"picks_without_entry_signal":no_entry,
        "bullish":metrics(df[df.side=="bull"]) if not df.empty else {"trades":0},
        "bearish":metrics(df[df.side=="bear"]) if not df.empty else {"trades":0},
        "combined":metrics(df),
        "data_source":"Yahoo Finance chart endpoint, 5-minute interval; unofficial third-party data, subject to gaps, adjustments, throttling and retention limits.",
        "limitations":[
            "This tests a specific mechanical entry rule, not every possible intraday entry.",
            "No brokerage, STT, exchange charges, GST, stamp duty, taxes or slippage are deducted.",
            "Yahoo 5-minute history may be incomplete or unavailable for some symbols/dates; missing counts are reported.",
            "No trade is entered if the entry condition does not occur; this is not a guarantee of fills at next candle open.",
            "Historical universe is inherited from V3's EQ stocks with active stock futures; survivorship and contract changes may remain.",
            "Paper trading and independent data validation are required before live use."
        ]
    }
    (OUT/"summary_v4.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    (OUT/"data_gaps_v4.json").write_text(json.dumps(errors,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))
if __name__=="__main__": main()
