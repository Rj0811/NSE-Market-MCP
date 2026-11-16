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
- Workflow: **NSE Historical Scanner Backtest** (`.github/workflows/backtest-scanner.yml`)
- Default: 20 signal sessions, restricted to cash EQ stocks with a matching active stock-futures contract
- Sources: NSE full cash bhavcopy with delivery and official UDiFF F&O bhavcopy archives
- V2 ranking: cash price/volume/delivery only (maximum 35 points)
- Futures: positive futures price return is a confirmation gate; OI change is shown as context, not scored
- Options: near-ATM option OI is retained for research context but excluded from ranking
- Reports: top-5/top-10 confirmed picks, cash-only comparison, legacy three-module comparison, and active stock-futures universe baseline

### Run on GitHub from Android

1. Open **Actions** in this repository.
2. Select **NSE Historical Scanner Backtest**.
3. Tap **Run workflow** and leave sessions at **20**.
4. Open the completed run and download `nse-scanner-backtest-...`.
5. Start with `backtest-output/summary.json`, then inspect `top5_daily_picks_v2.csv` and `all_scored_stocks_v2.csv`.

The workflow retrieves five warm-up sessions, the requested signal sessions, and one following session for outcomes (26 complete sessions for the default 20-session test). NSE holidays and missing archive dates are skipped. If the required sessions are not available in the 90-calendar-day window, the run fails with a clear message.

**Methodology limitations:** this version uses end-of-day data only. It does not have historical intraday option-chain snapshots or historical pre-open snapshots, and does not include brokerage, taxes, slippage, or execution simulation. The cash score and futures confirmation rule are fixed heuristics, not an optimized model. Treat results as an initial research test, not evidence of future profitability. NSE data is subject to NSE's applicable terms and rights.
