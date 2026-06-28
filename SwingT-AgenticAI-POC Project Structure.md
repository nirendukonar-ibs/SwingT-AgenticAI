
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