# SwingTradeIQ — Claude Code Project

## What this project is
A 10-agent multi-agent swing trading system for Indian equity markets (NSE).
Built as part of Advanced Business Analytics MBA course at IBS India, Kolkata.
Companion project: BrandPulse IQ (Marketing track, separate folder).

## Architecture
10 specialist agents + backtest engine. Each agent: Python class in agents/
with pattern __init__() → private methods → run().

Agent pipeline (in order):
1. DataCollectorAgent      → agents/data_collector.py      (Session 1  ✅)
2. QualityValidatorAgent   → agents/quality_validator.py   (Session 2  ✅)
3. EDAAgent                → agents/eda_agent.py           (Session 3  ✅)
4. FundamentalAgent        → agents/fundamental_agent.py   (Session 4  ✅)
5. TechnicalAgent          → agents/technical_agent.py     (Session 5  ✅)
6. IndicatorEngine         → agents/indicator_engine.py    (Session 6  ✅)
7. RiskAgent               → agents/risk_agent.py          (Session 7  ✅)
8. PositionSizerAgent      → agents/position_sizer.py      (Session 8  ✅)
9. PortfolioManagerAgent   → agents/portfolio_manager.py   (Session 9  ✅)
10. ReportAgent            → agents/report_agent.py        (Session 10 ✅)

BacktestEngine             → agents/backtest_engine.py     (Session 10 ✅)
Orchestrator               → swingtrade_iq.py              (Session 10 ✅)

## Agent responsibilities (one-liner each)
1. DataCollector      — fetches OHLCV + metadata from yfinance; writes data/raw/
2. QualityValidator   — 8 checks (gaps, spikes, staleness, OHLC logic); repairs & writes data/validated/
3. EDA                — log-returns, annualised vol/return/Sharpe, max drawdown, correlation matrix
4. Fundamental        — sector-aware scoring: PE/PB/ROE/D-E/gross margin/current ratio/div yield
5. Technical          — SMA/EMA/MACD/RSI/Stochastic/BB/ATR/ADX/OBV via `ta` library; 6-component score
6. IndicatorEngine    — MACD/RSI/BB pattern detection; ATR trade levels; combined signal (Tech+Fund)
7. RiskAgent          — VaR/CVaR (95%); hard/soft rejection rules; per-position size multiplier
8. PositionSizer      — Kelly criterion (half-Kelly, trade variant); min(kelly, risk-budget) shares
9. PortfolioManager   — portfolio-level metrics (Markowitz vol, Sharpe, beta); scenario analysis; ranked orders
10. ReportAgent       — HTML report (21 KB), JSON summary, CSV trade orders
BacktestEngine        — walk-forward portfolio sim; 0.2% cost/leg; MACD cross signal; equity curve + metrics

## Data flow
data/raw/          ← DataCollectorAgent writes here
data/validated/    ← QualityValidatorAgent writes here
data/eda/          ← EDAAgent writes here
data/fundamental/  ← FundamentalAgent writes here
data/technical/    ← TechnicalAgent writes here
data/signals/      ← IndicatorEngine writes here
data/risk/         ← RiskAgent writes here
data/positions/    ← PositionSizerAgent writes here
data/portfolio/    ← PortfolioManagerAgent writes here
data/backtest/     ← BacktestEngine writes here
data/meta/         ← universe_meta.json (from DataCollector)
outputs/           ← Final reports (HTML, JSON, CSV) — gitignored
logs/              ← Per-agent run logs

## Key output files (quick reference)
outputs/report_YYYY-MM-DD.json          — master daily report (read after every scan)
data/portfolio/portfolio_state.json     — final positions, capital table, sector allocation
data/portfolio/trade_orders.json        — priority-ordered executable buy orders
data/portfolio/scenario_analysis.json  — bear / bull T1 / bull T2 / expected value
data/portfolio/portfolio_metrics.json  — Sharpe, beta, vol, diversification ratio
data/backtest/backtest_START_END.json  — backtest results + full trade log

## Signal logic & scoring weights
Combined score  = tech_score × 0.60  +  fund_score × 0.40   (both 0–10)
SWING_BUY       if combined ≥ 6.0 AND fundamental passed (score ≥ min_fundamental_score)
WATCH           if combined ≥ 4.5
NO_TRADE        otherwise
ATR trade levels: stop = entry − 2×ATR14 | T1 = entry + 4×ATR14 | T2 = entry + 6×ATR14
Risk hard rules : R/R < 1.5 OR max_dd < −50% OR ATR% > 8%  → REJECTED
Risk soft rules : max_dd < −30%, ATR% > 4%, Sharpe < 0  → each applies ×0.75 size multiplier
Kelly sizing    : half-Kelly (fraction=0.5); uses trade Kelly = win_rate − loss_rate/R_ratio
Position cap    : min(kelly_shares, risk_budget_shares); hard cap at 20% equity per position

## config.yaml key fields
portfolio.total_capital         — trading capital in INR (default 200000)
portfolio.max_open_positions    — simultaneous position cap (default 8)
portfolio.max_sector_exposure   — max % capital in one sector (default 0.30)
portfolio.risk_per_trade        — % capital risked per trade (default 0.02)
portfolio.kelly_fraction        — half-Kelly multiplier (default 0.5; reduce to 0.4 if WR < 45%)
filters.min_fundamental_score   — minimum score to qualify for a trade (default 5.0)

## Coding conventions
- Every agent MUST have: __init__(), run(), and save its outputs
- run() MUST return dict with keys: succeeded, failed, output_paths, elapsed_s
- All file paths use pathlib.Path — never string concatenation
- NSE tickers stored WITH .NS suffix internally; strip for display only
- auto_adjust=True always when calling yfinance.download()
- No print() in agent methods — use self._log() instead
- All outputs JSON-serialisable (use default=str in json.dump)

## Python environment
Virtual environment: venv/ (never commit)
Activate: source venv/bin/activate
Install:  pip install -r requirements.txt

## Run commands
Full scan:     python swingtrade_iq.py --mode scan --capital 200000
Monitor:       python swingtrade_iq.py --mode monitor
Weekly review: python swingtrade_iq.py --mode review
Backtest:      python swingtrade_iq.py --mode backtest --start 2023-01-01 --end 2024-12-31

## Universe configuration (stock universe is fully configurable)
Default is Nifty 50 (50 stocks, bundled — no CSV needed). Priority order:
  --tickers INFY TCS RELIANCE  → explicit list (highest priority)
  --universe nifty100          → built-in Nifty 100 (bundled)
  --universe nifty500          → needs data/universe/nifty500.csv
  --csv /path/to/file.csv      → any CSV with a Symbol column
  config.yaml watchlist.universe     → nifty50 (default)
  config.yaml watchlist.csv_file     → path to a CSV
  config.yaml watchlist.custom_tickers → explicit list in config

Supported built-in names: nifty50, nifty100
Supported CSV aliases (need data/universe/{name}.csv downloaded from NSEIndia):
  nifty200, nifty500, nifty_midcap150, nifty_smallcap250, nifty250-400
  See data/universe/README.md for download instructions.

List all options: python swingtrade_iq.py --list-universes

## Slash commands (.claude/commands/)
/scan     — full daily pipeline; elaborate per-ticker signal + order report
/monitor  — position health check (Tue–Thu); alerts, live R, trailing stop guidance
/review   — weekly review (Friday); scorecard, Kelly calibration, next-week prep
/backtest — historical backtest; 9-section report incl. cost impact, Kelly check, insights

## Key constants (don't change without updating all agents)
MIN_HISTORY_ROWS = 100       # minimum OHLCV rows needed
DEFAULT_PERIOD_DAYS = 365    # history to fetch
NIFTY50_BENCHMARK = "^NSEI" # benchmark index ticker
NSE_CLOSE_TIME = "15:30"    # IST — run after this

## What NOT to do
- Never hardcode capital amounts in agent files — always read from config.yaml
- Never fetch data inside __init__() — only in run() or explicit fetch methods
- Never write to data/raw/ from any agent other than DataCollectorAgent
- Never delete files from data/ without explicit user instruction

## Current status
All 10 sessions + BacktestEngine complete ✅ — pipeline fully operational.
Tested on INFY, HDFCBANK, TCS, BAJAJ-AUTO (NSE).
Run the full system: python swingtrade_iq.py --mode scan --capital 200000

---

## Operating Instructions

### Trigger phrases → map to mode
- "scan / setups today / what should I buy" → /scan
- "check positions / monitor portfolio"      → /monitor
- "weekly review"                            → /review
- "backtest [dates]"                         → /backtest [start] [end]

### Capital & universe parsing
- "2 lakhs" → 200000 | "1.5 lakh" → 150000 | "50k" → 50000 | else read config.yaml
- "Nifty 50 / default" → `--universe nifty50` (or omit, it's the default)
- "Nifty 100" → `--universe nifty100`   |  "Nifty 500" → `--universe nifty500` (needs CSV)
- "my watchlist / custom stocks" → `--tickers TICKER1 TICKER2 …`
- "my CSV file" → `--csv path/to/file.csv`

### After every scan/monitor/review
1. Read the appropriate JSON output file (see Key output files above)
2. Present in the format defined in .claude/commands/[mode].md
3. Check references/risk-calendar.md for events within 5 trading days
4. Always append the disclaimer

### Error handling
| Error | Response |
|---|---|
| swingtrade_iq.py not found | "Not built yet — run Sessions 1–10 first" |
| yfinance fetch fails | Skip failed tickers, continue with rest |
| No signals generated | "No setups today — market likely ranging" |
| venv not activated | Run `source venv/bin/activate` first |

### Disclaimer (append to every output)
> ⚠️ SwingTradeIQ is for educational purposes only as part of IBS India MBA —
> Advanced Business Analytics. Not SEBI-registered investment advice.
> Consult a registered advisor before actual trading.
