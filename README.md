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
