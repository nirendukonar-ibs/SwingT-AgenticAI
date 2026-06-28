#!/usr/bin/env python3
"""
SwingTradeIQ — 10-agent NSE swing trading orchestrator.

Built as part of IBS India MBA — Advanced Business Analytics.

Modes
-----
  scan     Full pipeline from data fetch through report (default)
  monitor  Refresh signals without re-fetching data (agents 5–10)
  review   Alias for scan — full weekly re-run
  backtest Run historical backtest over a date range

Universe (in priority order — first match wins)
-------
  --tickers INFY TCS RELIANCE   explicit list
  --universe nifty100            named index  (nifty50 | nifty100 | nifty500 | …)
  --csv /path/to/stocks.csv      any CSV with a Symbol column
  config.yaml watchlist.csv_file
  config.yaml watchlist.custom_tickers
  config.yaml watchlist.universe  (default: nifty50)

Usage examples
--------------
  python swingtrade_iq.py --mode scan --capital 200000
  python swingtrade_iq.py --mode scan --universe nifty100
  python swingtrade_iq.py --mode scan --csv data/universe/my_picks.csv
  python swingtrade_iq.py --mode scan --tickers INFY TCS RELIANCE
  python swingtrade_iq.py --mode monitor
  python swingtrade_iq.py --mode review
  python swingtrade_iq.py --mode backtest --start 2023-01-01 --end 2024-12-31 --universe nifty50
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml


BASE_DIR = Path(__file__).parent


# ─── Config helpers ───────────────────────────────────────────────────────────

def load_config(capital_override: float | None = None) -> dict:
    cfg_path = BASE_DIR / 'config.yaml'
    if not cfg_path.exists():
        print(f"ERROR: config.yaml not found at {cfg_path}")
        sys.exit(1)
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    if capital_override is not None:
        cfg['portfolio']['total_capital'] = capital_override
    return cfg


def resolve_watchlist(cfg: dict,
                      tickers:  list[str] | None = None,
                      universe: str | None        = None,
                      csv_path: str | None        = None) -> list[str]:
    """
    Resolve the stock universe in priority order:
      1. --tickers (explicit CLI list)
      2. --universe <name>  or  --csv <path>  (CLI overrides)
      3. config.yaml watchlist.csv_file
      4. config.yaml watchlist.custom_tickers (if non-empty)
      5. config.yaml watchlist.universe       (named index, default nifty50)
    """
    from agents.universe_loader import UniverseLoader
    loader   = UniverseLoader(BASE_DIR)
    wl_cfg   = cfg.get('watchlist', {}) if isinstance(cfg.get('watchlist'), dict) else {}
    max_size = wl_cfg.get('max_universe_size') or None

    # 1. Explicit ticker list from CLI
    if tickers:
        from agents.universe_loader import UniverseLoader
        return UniverseLoader._clean(tickers)

    # 2. CLI --universe or --csv
    if universe:
        return loader.load(universe, max_size=max_size)
    if csv_path:
        return loader.load(csv_path, max_size=max_size)

    # 3. config.yaml csv_file
    csv_file = wl_cfg.get('csv_file', '').strip()
    if csv_file:
        return loader.load(csv_file, max_size=max_size)

    # 4. config.yaml custom_tickers explicit list
    custom = wl_cfg.get('custom_tickers', [])
    if custom:
        return loader.load('custom', custom=custom, max_size=max_size)

    # 5. config.yaml universe name  (default: nifty50)
    name = wl_cfg.get('universe', 'nifty50')
    return loader.load(name, max_size=max_size)


# ─── Agent runner helpers ─────────────────────────────────────────────────────

def _sep(title: str) -> None:
    width = 70
    print(f"\n{'━'*width}")
    print(f"  {title}")
    print(f"{'━'*width}")


def _ok(name: str, result: dict) -> None:
    elapsed = result.get('elapsed_s', '?')
    n_ok    = len(result.get('succeeded', []))
    n_fail  = len(result.get('failed', []))
    tag     = f"✅  {n_ok} ok" + (f"  ❌ {n_fail} failed" if n_fail else '')
    print(f"  {name:<30} {tag}  ({elapsed}s)")


# ─── Pipeline definitions ─────────────────────────────────────────────────────

def _run_data_collector(watchlist: list[str]) -> dict:
    from agents.data_collector import DataCollectorAgent
    agent = DataCollectorAgent(BASE_DIR)
    return agent.run(watchlist)


def _run_quality_validator() -> dict:
    from agents.quality_validator import QualityValidatorAgent
    agent = QualityValidatorAgent(BASE_DIR)
    return agent.run()


def _run_eda() -> dict:
    from agents.eda_agent import EDAAgent
    agent = EDAAgent(BASE_DIR)
    return agent.run()


def _run_fundamental() -> dict:
    from agents.fundamental_agent import FundamentalAgent
    agent = FundamentalAgent(BASE_DIR)
    return agent.run()


def _run_technical() -> dict:
    from agents.technical_agent import TechnicalAgent
    agent = TechnicalAgent(BASE_DIR)
    return agent.run()


def _run_indicator_engine() -> dict:
    from agents.indicator_engine import IndicatorEngine
    agent = IndicatorEngine(BASE_DIR)
    return agent.run()


def _run_risk() -> dict:
    from agents.risk_agent import RiskAgent
    agent = RiskAgent(BASE_DIR)
    return agent.run()


def _run_position_sizer() -> dict:
    from agents.position_sizer import PositionSizerAgent
    agent = PositionSizerAgent(BASE_DIR)
    return agent.run()


def _run_portfolio_manager() -> dict:
    from agents.portfolio_manager import PortfolioManagerAgent
    agent = PortfolioManagerAgent(BASE_DIR)
    return agent.run()


def _run_report() -> dict:
    from agents.report_agent import ReportAgent
    agent = ReportAgent(BASE_DIR)
    return agent.run()


# ─── Mode: scan / review (full pipeline) ─────────────────────────────────────

def mode_scan(cfg: dict, watchlist: list[str]) -> None:
    _sep("SCAN — Full pipeline (10 agents)")
    total_start = time.time()

    steps = [
        ("1/10  DataCollector",     lambda: _run_data_collector(watchlist)),
        ("2/10  QualityValidator",  _run_quality_validator),
        ("3/10  EDA",               _run_eda),
        ("4/10  FundamentalAgent",  _run_fundamental),
        ("5/10  TechnicalAgent",    _run_technical),
        ("6/10  IndicatorEngine",   _run_indicator_engine),
        ("7/10  RiskAgent",         _run_risk),
        ("8/10  PositionSizer",     _run_position_sizer),
        ("9/10  PortfolioManager",  _run_portfolio_manager),
        ("10/10 ReportAgent",       _run_report),
    ]

    results = {}
    for name, fn in steps:
        _sep(name)
        try:
            r = fn()
            _ok(name, r)
            results[name] = r
        except Exception as exc:
            print(f"  ❌  {name} FAILED: {exc}")
            results[name] = {'succeeded': [], 'failed': [], 'error': str(exc)}

    _print_pipeline_summary(results, total_start)


# ─── Mode: monitor (signals refresh, no data fetch) ──────────────────────────

def mode_monitor() -> None:
    _sep("MONITOR — Signal refresh (agents 5–10, no data fetch)")
    total_start = time.time()

    steps = [
        ("5/10  TechnicalAgent",   _run_technical),
        ("6/10  IndicatorEngine",  _run_indicator_engine),
        ("7/10  RiskAgent",        _run_risk),
        ("8/10  PositionSizer",    _run_position_sizer),
        ("9/10  PortfolioManager", _run_portfolio_manager),
        ("10/10 ReportAgent",      _run_report),
    ]

    results = {}
    for name, fn in steps:
        _sep(name)
        try:
            r = fn()
            _ok(name, r)
            results[name] = r
        except Exception as exc:
            print(f"  ❌  {name} FAILED: {exc}")
            results[name] = {'succeeded': [], 'failed': [], 'error': str(exc)}

    _print_pipeline_summary(results, total_start)


# ─── Mode: backtest ──────────────────────────────────────────────────────────

def mode_backtest(start: str, end: str, capital: float,
                  watchlist: list[str]) -> None:
    from agents.backtest_engine import BacktestEngine
    _sep("BACKTEST — Historical simulation")
    engine = BacktestEngine(
        base_dir   = BASE_DIR,
        start_date = start,
        end_date   = end,
        capital    = capital,
        tickers    = watchlist,
    )
    engine.run()


# ─── Summary ─────────────────────────────────────────────────────────────────

def _print_pipeline_summary(results: dict, total_start: float) -> None:
    elapsed = time.time() - total_start
    _sep("PIPELINE SUMMARY")
    ok, fail = 0, 0
    for name, r in results.items():
        n_ok   = len(r.get('succeeded', []))
        n_fail = len(r.get('failed', []))
        err    = r.get('error', '')
        icon   = '✅' if not err else '❌'
        ok    += n_ok
        fail  += n_fail
        print(f"  {icon}  {name:<30} ok={n_ok}  fail={n_fail}"
              + (f"  [{err}]" if err else ''))

    print(f"\n  Total time  : {elapsed:.1f}s")
    print(f"  Succeeded   : {ok} tickers across all agents")
    if fail:
        print(f"  Failed      : {fail}")

    # Show output files if report ran
    port_path = BASE_DIR / 'data' / 'portfolio' / 'portfolio_state.json'
    if port_path.exists():
        state = json.load(open(port_path))
        ct    = state.get('capital_table', {})
        print(f"\n  ── Final portfolio ──────────────────────────────────")
        print(f"  Deployed  ₹{ct.get('total_deployed',0):>10,.0f}  ({ct.get('deployed_pct',0):.1f}%)")
        print(f"  Cash      ₹{ct.get('remaining_cash',0):>10,.0f}  ({ct.get('cash_pct',0):.1f}%)")
        print(f"  At risk   ₹{ct.get('total_risk',0):>10,.0f}  ({ct.get('risk_pct',0):.2f}%)")
        for p in state.get('positions', []):
            fin = p.get('final', {})
            print(f"    {p['ticker']:<14} {p.get('signal',''):<12}"
                  f" shares={fin.get('shares','?'):>3}"
                  f" ₹{fin.get('position_value_inr',0):>10,.0f}"
                  f" sl=₹{fin.get('stop_loss',0):,.2f}"
                  f" t1=₹{fin.get('target_1',0):,.2f}")

    out_dir = BASE_DIR / 'outputs'
    reports = sorted(out_dir.glob('report_*.html'), reverse=True)
    if reports:
        print(f"\n  → Report : {reports[0]}")


# ─── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='SwingTradeIQ — NSE multi-agent swing trading system',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--mode', choices=['scan', 'monitor', 'review', 'backtest'],
                        default='scan', help='Pipeline mode (default: scan)')
    parser.add_argument('--capital', type=float, default=None,
                        help='Override total capital from config.yaml (INR)')
    parser.add_argument('--tickers', nargs='+', default=None,
                        help='Explicit ticker list (highest priority)')
    parser.add_argument('--universe', default=None,
                        help='Named index: nifty50 | nifty100 | nifty500 | …')
    parser.add_argument('--csv', default=None, dest='csv_path',
                        help='Path to CSV file with a Symbol column')
    parser.add_argument('--start', default=None,
                        help='Backtest start date YYYY-MM-DD')
    parser.add_argument('--end', default=None,
                        help='Backtest end date YYYY-MM-DD')
    parser.add_argument('--list-universes', action='store_true',
                        help='Show all available universe options and exit')
    args = parser.parse_args()

    if args.list_universes:
        from agents.universe_loader import UniverseLoader
        UniverseLoader(BASE_DIR).describe()
        sys.exit(0)

    print(f"{'═'*70}")
    print(f"  SwingTradeIQ  ·  NSE Swing Trading System  ·  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  IBS India MBA — Advanced Business Analytics")
    print(f"{'═'*70}")

    cfg = load_config(args.capital)
    try:
        watchlist = resolve_watchlist(
            cfg,
            tickers  = args.tickers,
            universe = args.universe,
            csv_path = args.csv_path,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"\nERROR resolving universe:\n  {e}")
        sys.exit(1)
    capital = cfg['portfolio']['total_capital']

    src = (f"--tickers"           if args.tickers  else
           f"--universe {args.universe}" if args.universe else
           f"--csv {args.csv_path}"      if args.csv_path else
           f"config.yaml ({cfg.get('watchlist',{}).get('universe','nifty50')})")
    print(f"  Mode      : {args.mode}")
    print(f"  Capital   : ₹{capital:,.0f}")
    print(f"  Universe  : {src}  →  {len(watchlist)} tickers"
          + (f"  [{', '.join(watchlist[:5])}{'…' if len(watchlist)>5 else ''}]" if watchlist else ""))

    if args.mode in ('scan', 'review'):
        mode_scan(cfg, watchlist)
    elif args.mode == 'monitor':
        mode_monitor()
    elif args.mode == 'backtest':
        if not args.start or not args.end:
            print("ERROR: --start and --end are required for backtest mode")
            sys.exit(1)
        mode_backtest(args.start, args.end, capital, watchlist)


if __name__ == '__main__':
    main()
