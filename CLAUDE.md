# SwingTradeIQ — Claude Code Project

## What this project is
A 10-agent multi-agent swing trading system for Indian equity markets (NSE).
Built as part of Advanced Business Analytics MBA course at IBS India, Kolkata.
Companion project: BrandPulse IQ (Marketing track, separate folder).

## Architecture
10 specialist agents built across 12 sessions. Each agent is a Python class
in agents/ with the same pattern: __init__() → private methods → run().

Agent pipeline (in order):
1. DataCollectorAgent      → agents/data_collector.py      (Session 1 ✅)
2. QualityValidatorAgent   → agents/quality_validator.py   (Session 2)
3. EDAAgent                → agents/eda_agent.py           (Session 3)
4. FundamentalAgent        → agents/fundamental_agent.py   (Session 4)
5. TechnicalAgent          → agents/technical_agent.py     (Session 5)
6. IndicatorEngine         → agents/indicator_engine.py    (Session 6)
7. RiskAgent               → agents/risk_agent.py          (Session 7)
8. PositionSizerAgent      → agents/position_sizer.py      (Session 8)
9. PortfolioManagerAgent   → agents/portfolio_manager.py   (Session 9)
10. ReportAgent            → agents/report_agent.py        (Session 10)

Orchestrator:              → swingtrade_iq.py              (Session 10)

## Data flow
data/raw/          ← DataCollectorAgent writes here
data/validated/    ← QualityValidatorAgent writes here
data/eda/          ← EDAAgent writes here
data/fundamental/  ← FundamentalAgent writes here
data/technical/    ← TechnicalAgent writes here
data/signals/      ← IndicatorEngine writes here
data/risk/         ← RiskAgent writes here
data/meta/         ← universe_meta.json (from DataCollector)
outputs/           ← Final reports (HTML, PDF, JSON, CSV)
logs/              ← Per-agent run logs

## Coding conventions
- Every agent class MUST have: __init__(), run(), and save its outputs
- run() MUST return a dict with keys: succeeded, failed, output_paths, elapsed_s
- All file paths use pathlib.Path — never string concatenation
- NSE tickers always stored WITH .NS suffix internally
- auto_adjust=True always when calling yfinance.download()
- No print statements in agent methods — use self._log() instead
- All outputs are JSON-serialisable (use default=str in json.dump)

## Python environment
Virtual environment: venv/ (never commit this)
Activate before running: source venv/bin/activate
Install deps: pip install -r requirements.txt

## Run commands
Full scan:     python swingtrade_iq.py --mode scan --capital 200000
Monitor:       python swingtrade_iq.py --mode monitor
Weekly review: python swingtrade_iq.py --mode review
Backtest:      python swingtrade_iq.py --mode backtest --start 2024-01-01 --end 2024-12-31

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
- Don't add features not yet in the session plan — build in session order

## Current status
Session 1 complete: DataCollectorAgent ✅
Sessions 2-10: Not yet built — build in order, one per session