# SwingTradeIQ

A 10-agent multi-agent swing trading system for Indian equity markets (NSE).

Built as part of the **Advanced Business Analytics** MBA course at **IBS India, Kolkata**.

> **Educational use only.** Not SEBI-registered investment advice.

---

## What it does

SwingTradeIQ runs a fully automated pipeline that:

1. Fetches OHLCV data for any NSE index (Nifty 50 by default, configurable up to Nifty 500)
2. Validates data quality across 8 checks
3. Computes EDA metrics — volatility, Sharpe, drawdown, correlation
4. Scores each stock on fundamentals (PE/PB/ROE/D-E/margins) and technicals (MACD/RSI/BB/ADX)
5. Generates combined signals: **SWING_BUY**, **WATCH**, or **NO_TRADE**
6. Sizes positions using Kelly criterion with ATR-based stop/target levels
7. Builds a portfolio-level view (Markowitz volatility, sector exposure, scenario analysis)
8. Produces an HTML report, JSON summary, and CSV trade orders
9. Runs a walk-forward backtest with 0.2% per-leg transaction cost

---

## Requirements

- Python **3.10+** (tested on 3.12.5)
- Internet connection (live data from yfinance)
- macOS / Linux / Windows (WSL recommended on Windows)

---

## Installation

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd SwingT-AgenticAI-POC
```

### 2. Create a virtual environment

```bash
python3 -m venv venv
```

### 3. Activate the virtual environment

**macOS / Linux:**
```bash
source venv/bin/activate
```

**Windows (Command Prompt):**
```cmd
venv\Scripts\activate.bat
```

**Windows (PowerShell):**
```powershell
venv\Scripts\Activate.ps1
```

You should see `(venv)` in your terminal prompt after activation.

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

This installs ~40 packages including `yfinance`, `ta`, `pandas`, `numpy`, `matplotlib`, `scikit-learn`, `PyYAML`, and `weasyprint`.

> **Note for macOS users:** If `weasyprint` fails, install system dependencies first:
> ```bash
> brew install pango gdk-pixbuf libffi cairo gobject-introspection
> ```

> **Note for Linux users:**
> ```bash
> sudo apt-get install python3-dev libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0
> ```

### 5. Verify installation

```bash
python swingtrade_iq.py --help
```

You should see the usage documentation with all available flags.

---

## Configuration

All settings live in `config.yaml`. Key values:

```yaml
portfolio:
  total_capital: 200000        # INR — override with --capital flag
  max_open_positions: 8
  risk_per_trade: 0.02         # 2% of capital risked per trade
  kelly_fraction: 0.5          # half-Kelly position sizing

watchlist:
  universe: nifty50            # default stock universe
  csv_file: ""                 # path to custom CSV (optional)
  custom_tickers: []           # explicit ticker list (optional)
  max_universe_size: 50        # safety cap

filters:
  min_fundamental_score: 5.0   # minimum score to generate a signal
```

---

## Running the system

**Always activate the virtual environment first:**
```bash
source venv/bin/activate
```

### Full daily scan (run after 3:30 PM IST)

```bash
python swingtrade_iq.py --mode scan --capital 200000
```

### Monitor open positions (Tuesday–Thursday)

```bash
python swingtrade_iq.py --mode monitor
```

### Weekly review (Friday)

```bash
python swingtrade_iq.py --mode review
```

### Run a backtest

```bash
python swingtrade_iq.py --mode backtest --start 2023-01-01 --end 2024-12-31
```

---

## Stock universe options

The default is Nifty 50 (50 stocks, no setup required). You can override this in three ways:

### 1. CLI flag — highest priority

```bash
# Built-in indices (no download needed):
python swingtrade_iq.py --mode scan --universe nifty50     # 50 stocks
python swingtrade_iq.py --mode scan --universe nifty100    # 100 stocks

# Explicit list of tickers:
python swingtrade_iq.py --mode scan --tickers INFY TCS RELIANCE HDFCBANK

# Your own CSV file:
python swingtrade_iq.py --mode scan --csv /path/to/my_stocks.csv
```

### 2. Larger indices (Nifty 200, 500 etc.)

For indices beyond Nifty 100, download the CSV from NSEIndia:

1. Go to [NSEIndia Live Equity Market](https://www.nseindia.com/market-data/live-equity-market)
2. Select the index from the dropdown (e.g. "NIFTY 500")
3. Click **Download (.csv)** at the top-right of the table
4. Save the file to `data/universe/` with the exact filename below:

| Universe flag              | Filename                    |
|----------------------------|-----------------------------|
| `--universe nifty200`      | `data/universe/nifty200.csv` |
| `--universe nifty500`      | `data/universe/nifty500.csv` |
| `--universe nifty_midcap150` | `data/universe/nifty_midcap150.csv` |
| `--universe nifty_smallcap250` | `data/universe/nifty_smallcap250.csv` |

Then run:
```bash
python swingtrade_iq.py --mode scan --universe nifty500
```

### 3. config.yaml (persistent default)

Edit `config.yaml` to change the default for all future runs:

```yaml
watchlist:
  universe: nifty100          # change this
  custom_tickers: [INFY, TCS] # or use this for a custom list
```

### List all available options

```bash
python swingtrade_iq.py --list-universes
```

---

## Output files

After a scan, results are written to:

| File | Contents |
|------|----------|
| `outputs/report_YYYY-MM-DD.json` | Master daily report |
| `outputs/report_YYYY-MM-DD.html` | Visual HTML report (open in browser) |
| `data/portfolio/trade_orders.json` | Priority-ordered executable buy orders |
| `data/portfolio/portfolio_state.json` | Final positions, capital table, sector allocation |
| `data/portfolio/scenario_analysis.json` | Bear / bull T1 / bull T2 / expected value |
| `data/portfolio/portfolio_metrics.json` | Sharpe, beta, volatility, diversification ratio |
| `data/backtest/backtest_START_END.json` | Backtest results + full trade log |

---

## Project structure

```
SwingT-AgenticAI-POC/
├── swingtrade_iq.py          # Orchestrator — entry point
├── config.yaml               # All configuration
├── requirements.txt          # Python dependencies
│
├── agents/                   # 10 specialist agents
│   ├── universe_loader.py    # Stock universe resolver
│   ├── data_collector.py     # Agent 1 — OHLCV fetch
│   ├── quality_validator.py  # Agent 2 — 8-check data validation
│   ├── eda_agent.py          # Agent 3 — exploratory analysis
│   ├── fundamental_agent.py  # Agent 4 — fundamental scoring
│   ├── technical_agent.py    # Agent 5 — technical indicators
│   ├── indicator_engine.py   # Agent 6 — pattern detection & signals
│   ├── risk_agent.py         # Agent 7 — VaR/CVaR, hard/soft rules
│   ├── position_sizer.py     # Agent 8 — Kelly criterion sizing
│   ├── portfolio_manager.py  # Agent 9 — portfolio metrics & orders
│   ├── report_agent.py       # Agent 10 — HTML/JSON/CSV reports
│   └── backtest_engine.py    # Walk-forward backtest engine
│
├── data/                     # Pipeline data store (gitignored except structure)
│   ├── universe/             # Place Nifty CSV files here for large indices
│   ├── raw/                  # OHLCV data (gitignored)
│   ├── validated/            # Quality-checked data
│   ├── eda/                  # EDA results
│   ├── fundamental/          # Fundamental scores
│   ├── technical/            # Technical indicators
│   ├── signals/              # Combined signals
│   ├── risk/                 # Risk assessments
│   ├── positions/            # Position sizes
│   ├── portfolio/            # Portfolio state & orders
│   └── backtest/             # Backtest results
│
├── outputs/                  # Final reports — gitignored
├── logs/                     # Per-agent run logs
└── references/               # Reference docs (indicators, Kelly, NSE quirks)
```

---

## Signal logic

```
Combined score  = Technical score × 0.60  +  Fundamental score × 0.40
                  (both scored 0–10)

SWING_BUY  →  combined ≥ 6.0  AND  fundamental ≥ min_fundamental_score
WATCH      →  combined ≥ 4.5
NO_TRADE   →  otherwise

Trade levels (ATR-based):
  Stop loss  = entry − 2 × ATR14
  Target 1   = entry + 4 × ATR14     (primary exit, 2:1 R/R)
  Target 2   = entry + 6 × ATR14     (trail after T1)

Risk rules:
  REJECTED if  R/R < 1.5  OR  max drawdown < −50%  OR  ATR% > 8%
  Size ×0.75 if  max DD < −30%  OR  ATR% > 4%  OR  Sharpe < 0
```

---

## Claude Code slash commands

If using this project with Claude Code, four slash commands are available:

| Command | When to use |
|---------|-------------|
| `/scan` | Daily scan after 3:30 PM IST — full signal + order report |
| `/monitor` | Tuesday–Thursday — position health, alerts, trailing stop guidance |
| `/review` | Friday — weekly scorecard, Kelly calibration, next-week prep |
| `/backtest` | Historical backtest — 9-section report with cost analysis |

---

## Disclaimer

> SwingTradeIQ is built for **educational purposes only** as part of the IBS India MBA —
> Advanced Business Analytics course. Signal scores, Kelly calculations, and trade
> recommendations are generated by an automated algorithm using publicly available data.
> This is **not SEBI-registered investment advice**. Past backtest performance does not
> guarantee future results. Verify all prices and corporate actions independently before
> placing any real orders. Consult a SEBI-registered investment advisor for personalised guidance.
