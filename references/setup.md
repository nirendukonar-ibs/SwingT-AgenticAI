# SwingTradeIQ — Setup Guide

---

## Prerequisites

- Python 3.9 or higher
- pip
- Internet connection (for yfinance data and Claude API)
- Anthropic API key (for ReportAgent LLM thesis generation)

---

## Installation

```bash
# 1. Clone or download the project
git clone https://github.com/[your-repo]/SwingTradeIQ.git
cd SwingTradeIQ

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set your Anthropic API key
export ANTHROPIC_API_KEY="sk-ant-..."
# OR add to ~/.bashrc / ~/.zshrc for persistence

# 4. Run setup script (creates folders, downloads Nifty500 tickers)
python setup.py

# 5. Verify installation
python scripts/check_env.py
```

Expected output from `check_env.py`:
```
✓ Python 3.11.2
✓ yfinance 0.2.38
✓ pandas 2.1.0
✓ ta 0.11.0
✓ anthropic 0.25.0
✓ weasyprint 60.1 (PDF generation)
✓ ANTHROPIC_API_KEY set
✓ config.yaml found
✓ Nifty500 tickers loaded (496 stocks)
✓ outputs/ directory ready
All checks passed. SwingTradeIQ is ready.
```

---

## requirements.txt

```
yfinance>=0.2.35
pandas>=2.0.0
numpy>=1.24.0
ta>=0.11.0
anthropic>=0.25.0
scikit-learn>=1.3.0
weasyprint>=60.0
pyyaml>=6.0
requests>=2.31.0
matplotlib>=3.7.0
seaborn>=0.12.0
jinja2>=3.1.0
```

---

## Project Structure

```
~/SwingTradeIQ/
├── swingtrade_iq.py          ← main entry point
├── config.yaml               ← user configuration
├── requirements.txt
├── setup.py
│
├── agents/
│   ├── data_collector.py
│   ├── quality_validator.py
│   ├── eda_agent.py
│   ├── fundamental_agent.py
│   ├── technical_agent.py
│   ├── indicator_engine.py
│   ├── risk_agent.py
│   ├── position_sizer.py
│   ├── portfolio_manager.py
│   └── report_agent.py
│
├── scripts/
│   ├── check_env.py
│   ├── patch_config.py
│   └── update_tickers.py     ← run quarterly to refresh Nifty500 list
│
├── data/
│   ├── raw/                  ← OHLCV CSVs from DataCollector
│   ├── validated/            ← cleaned data from QualityValidator
│   ├── eda/                  ← EDA profiles
│   ├── fundamental/          ← fundamental scores
│   ├── technical/            ← technical analysis results
│   ├── signals/              ← indicator scores
│   ├── risk/                 ← risk assessments
│   └── universe/
│       ├── nifty500_tickers.csv
│       └── nse_holidays_2025.csv
│
├── outputs/                  ← reports land here
│   ├── report_YYYY-MM-DD.html
│   ├── report_YYYY-MM-DD.json
│   ├── signals_YYYY-MM-DD.csv
│   ├── portfolio_current.json
│   └── performance_log.csv
│
└── templates/
    └── report_template.html  ← Jinja2 template for HTML report
```

---

## Automating the Daily Run (Optional)

### macOS / Linux (cron)
```bash
# Run scan every weekday at 4:00 PM IST (10:30 UTC)
crontab -e

# Add this line:
30 10 * * 1-5 cd ~/SwingTradeIQ && python swingtrade_iq.py --mode scan >> logs/cron.log 2>&1
```

### Windows (Task Scheduler)
Create a Basic Task → Daily → 4:00 PM → Action: Start Program → python → Arguments: `C:\SwingTradeIQ\swingtrade_iq.py --mode scan`

---

## First Run Checklist

1. `python setup.py` completed without errors
2. `python scripts/check_env.py` shows all green
3. `config.yaml` reviewed — capital, watchlist, risk settings confirmed
4. Test run: `python swingtrade_iq.py --mode scan --capital 100000`
5. Check `outputs/` folder — HTML report generated
6. Open report in browser — verify charts render correctly

If any step fails, check `logs/setup.log` for details.
