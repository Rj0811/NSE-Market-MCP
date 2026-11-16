# NSE-Market-MCP

A Python client that connects to the **official NSE-hosted remote MCP servers** and runs through GitHub Actions. It does not scrape NSE pages and does not host a persistent MCP server.

## Official NSE endpoints

- **Historical end-of-day data (Bhavcopy):** https://mcp.nseindia.in/bhavcopy/cm/mcp
- **CM market data:** https://mcp.nseindia.in/cmmkt/mcp

NSE describes the data as free to access without an API key. Intraday data is delayed by approximately 1–3 minutes; it is not tick-by-tick or order-book data. Use is subject to NSE's terms and informational-use restrictions. Read the official page and terms before using the data: https://www.nseindia.in/nse-mcp

## Run from GitHub on Android

1. Open this repository in GitHub.
2. Open **Actions**.
3. Select **NSE MCP**.
4. Tap **Run workflow**.
5. Keep mode as **discover**, server as **both**, then run.
6. Open the completed run and download the artifact named `nse-mcp-output-...`.
7. Inside it, open `tools-catalog.json` to see the exact tool names and required argument schemas published by NSE.

To call a tool:

1. Run the workflow again.
2. Select mode **call**.
3. Select **bhavcopy** or **cmmkt**.
4. Enter the exact tool name from the catalog.
5. Enter its arguments as JSON matching that tool's `inputSchema`.
6. Download the artifact and inspect `nse-result.json`.

## Local usage

Python 3.11+ is recommended.

```bash
pip install -r requirements.txt
python scripts/nse_mcp.py --mode discover --server both --output output/tools-catalog.json
```

Example tool invocation (replace the tool and arguments with the exact schema from the discovery output):

```bash
python scripts/nse_mcp.py --mode call --server bhavcopy --tool get_stock_history --args '{"symbol":"RELIANCE"}' --output output/nse-result.json
```

The example arguments are illustrative. Always use the current schema in the discovery output; NSE may change tool names or required parameters.

## Automation

The workflow can also refresh the tool catalog on weekdays at 11:30 UTC (17:00 India Standard Time). GitHub scheduled workflows are best-effort and may be delayed. They are not a continuously running service.

## Important limitations

- **This repository is an MCP client, not an MCP server.** GitHub Actions starts a temporary runner, connects to NSE's hosted MCP endpoint, saves the result as a workflow artifact, and exits.
- It does **not** create a permanent HTTPS API endpoint for ChatGPT to call.
- It does **not** connect this custom repository to the ChatGPT Android app. ChatGPT must support and have access to the relevant custom connector/integration separately.
- Do not put GitHub tokens, passwords, or other secrets in source files, workflow inputs, or public artifacts.
- The repository is currently public. Do not commit personal data or credentials.

## Disclaimer

For informational and educational use. Market data can be delayed, incomplete, or revised. Outputs are not investment advice or a recommendation to trade. Follow NSE's applicable terms and usage restrictions.


## Pre-open history collector

A separate GitHub Actions workflow archives the NSE pre-open API response and a normalized CSV:

- Workflow: **NSE Pre-open Archive** (`.github/workflows/preopen-archive.yml`)
- Collector: `scripts/preopen_collector.py`
- Schedule: weekdays at 09:10 IST (03:40 UTC), best-effort GitHub Actions scheduling
- Output: `data/preopen/YYYY/MM/YYYY-MM-DD.json` and `.csv`

To run it manually on Android, open **Actions → NSE Pre-open Archive → Run workflow**. The workflow commits the retrieved snapshot into this repository.

The collector reads the NSE pre-open page to establish a session, then requests the NSE pre-open market API. It saves the raw response as well as the per-security metadata rows. The saved timestamp is the collector retrieval time, not a guarantee of the exact exchange publication time.

**Historical limitation:** this collector starts building an archive from the first successful run. It does not backfill old pre-open order-book snapshots. Public third-party archives may be incomplete and should be independently validated. GitHub scheduled runs can be delayed, so this is not a guaranteed 09:10 capture service.

The pre-open auction is an indication of opening demand/supply, not a prediction of the full trading session. Orders can be modified or cancelled before matching. Use the data for research and paper testing only.


## Historical scanner backtest

A research-only backtest is available:

- Script: `scripts/backtest_scanner.py`
- Workflow: **NSE Historical Scanner Backtest V2** (`.github/workflows/backtest-scanner.yml`)
- Default: 20 signal sessions, restricted to cash EQ stocks with a matching active stock-futures contract
- Sources: NSE full cash bhavcopy with delivery and official UDiFF F&O bhavcopy archives
- V2 ranking: cash price/volume/delivery only (maximum 35 points)
- Futures: positive futures price return is a confirmation gate; OI change is shown as context, not scored
- Options: near-ATM option OI is retained for research context but excluded from ranking
- Reports: top-5/top-10 confirmed picks, cash-only comparison, legacy three-module comparison, and active stock-futures universe baseline

### Run on GitHub from Android

1. Open **Actions** in this repository.
2. Select **NSE Historical Scanner Backtest V2**.
3. Tap **Run workflow** and leave sessions at **20**.
4. Open the completed run and download `nse-scanner-backtest-v2-...`.
5. Start with `backtest-output/summary.json`, then inspect `top5_daily_picks_v2.csv` and `all_scored_stocks_v2.csv`.

The workflow retrieves five warm-up sessions, the requested signal sessions, and one following session for outcomes (26 complete sessions for the default 20-session test). NSE holidays and missing archive dates are skipped. If the required sessions are not available in the 90-calendar-day window, the run fails with a clear message.

**Methodology limitations:** this version uses end-of-day data only. It does not have historical intraday option-chain snapshots or historical pre-open snapshots, and does not include brokerage, taxes, slippage, or execution simulation. The cash score and futures confirmation rule are fixed heuristics, not an optimized model. Treat results as an initial research test, not evidence of future profitability. NSE data is subject to NSE's applicable terms and rights.


## Historical scanner V3: bullish/bearish shortlist and next-day range

- Script: `scripts/backtest_scanner_v3.py`
- Workflow: **NSE Historical Scanner Backtest V3** (`.github/workflows/backtest-scanner-v3.yml`)
- Default: 40 signal sessions, 20 prior sessions for moving averages, volume and delivery baselines, plus the next session for outcomes.
- Universe: cash EQ stocks with a matching active stock-futures contract on each signal date.
- Score: 100 points across trend/20-session breakout (25), volume vs prior 20 sessions (20), delivery level/change vs the stock's own 20-session average (20), futures price/OI matrix (25), and options PCR context (10).
- Outputs: top five bullish and bearish picks per signal session, full scored universe, and summary metrics.

### Next-session opportunity metrics

For each pick V3 reports overnight gap, open-to-close and previous-close-to-close return, plus maximum favourable and adverse excursion from the next session's open. It also reports whether the daily high/low reached +1%, +2% and +3% range thresholds for the relevant direction.

**Important:** daily OHLC cannot show whether the high or low happened first. A +2% range hit is not proof that a +2% target was reached before a stop-loss. A true intraday target/stop simulation needs historical intraday candles (ideally 1-minute or 5-minute OHLCV) and an explicit entry, target, stop, costs and slippage model. V3 does not claim to simulate those fills.

### Run from Android

Open GitHub → this repository → Actions → **NSE Historical Scanner Backtest V3** → Run workflow. Choose 20 or 40 signal sessions. Download the completed artifact and start with `backtest-output-v3/summary.json`, `top5_bullish_v3.csv` and `top5_bearish_v3.csv`.

V3 currently uses a 20-session breakout rather than a 52-week breakout. Futures rollover/basis and historical intraday options-chain changes are not scored until a validated historical data source is available. Results are research-only and exclude transaction costs, taxes and execution impact.


## Intraday Scanner V4: 1% target with stop-loss

- Script: `scripts/backtest_scanner_v4.py`
- Workflow: **NSE Intraday Scanner Backtest V4** (`.github/workflows/intraday-scanner-v4.yml`)
- V4 first rebuilds the V3 daily top-five bullish and bearish shortlist, then retrieves 5-minute candles for those symbols.
- Default target: 1.0%; default stop-loss: 0.5% (2:1 reward-to-risk before costs). Workflow also permits 0.75% or 1.0% stop-loss sensitivity tests.
- Entry: after 09:30 IST, first 5-minute candle close above/below cumulative VWAP and above previous candle high/below previous candle low; entry is assumed at the next candle open.
- Exit: target, stop-loss, or last available intraday candle close. If target and stop are both touched in one candle, stop-loss is assumed first.
- Data source for intraday bars: Yahoo Finance chart endpoint, 5-minute interval. This is an unofficial third-party source and may have missing symbols/dates, adjustments, throttling or retention limits. Missing data is counted and reported.
- Reports: `summary_v4.json`, `intraday_trades_v4.csv`, and `data_gaps_v4.json`.

### Run from Android

Open GitHub → Actions → **NSE Intraday Scanner Backtest V4** → Run workflow. Choose 40 sessions and the default 0.5% stop-loss. Download the artifact and inspect `backtest-output-v4/summary_v4.json` first.

**Limitations:** the entry rule is a mechanical test, not a promise of fills. Results are gross and do not deduct brokerage, STT, exchange charges, GST, stamp duty, taxes or slippage. Check data coverage before interpreting the win rate. Use paper trading and validate the intraday feed independently before any live use.
