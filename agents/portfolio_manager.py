# SwingTradeIQ — Agent 9/10: PortfolioManagerAgent
# Built in Session 9.
# Handoff: reads data/positions/ + data/risk/ + data/signals/ + data/eda/
#          + data/fundamental/ + data/technical/ + config.yaml
#          → writes data/portfolio/

import time, json
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import yaml

PRIORITY_MAP = {'SWING_BUY': 1, 'WATCH': 2, 'NO_TRADE': 3}
CONVICTION_MAP = {'HIGH': 1, 'MEDIUM': 2, 'LOW': 3, None: 4}

TRADING_DAYS = 252


class PortfolioManagerAgent:
    """
    Agent 9/10 — SwingTradeIQ Portfolio Manager Agent.

    Assembles individually sized positions (from PositionSizerAgent) into a
    coherent portfolio, enforces portfolio-level constraints that cannot be
    applied per-ticker in isolation, computes portfolio risk/return metrics
    using the EDA correlation matrix, and emits prioritised executable orders.

    Responsibilities
    ----------------
    1. Position selection & ranking
         Priority: SWING_BUY > WATCH; then conviction HIGH > MEDIUM > LOW;
         then combined score. Positions are included until max_open_positions
         or available capital is exhausted.

    2. Portfolio-level metrics (using EDA correlation matrix)
         • Weighted expected return
         • Portfolio volatility (Markowitz formula)
         • Portfolio Sharpe ratio
         • Beta-weighted market exposure
         • Diversification ratio

    3. Scenario analysis
         • Bear case: all stop losses triggered simultaneously
         • Bull case T1: all first targets hit
         • Bull case T2: all second targets hit
         • Expected value: (win_rate × avg_gain) − (loss_rate × avg_loss)

    4. Trade order generation
         One limit order per included position with full metadata:
         ticker, quantity, limit_price, stop_loss, target_1/2, priority,
         NSE exchange suffix, rationale.

    5. Capital allocation table
         Shows deployed / reserved / free cash, total risk, and per-sector
         exposure against the max_sector_exposure config limit.

    Outputs
    -------
        data/portfolio/portfolio_state.json   — master portfolio state
        data/portfolio/trade_orders.json      — ordered list of buy orders
        data/portfolio/portfolio_metrics.json — risk/return analytics
        data/portfolio/scenario_analysis.json — P&L scenarios
        logs/portfolio_{DATE}.txt             — run log
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir    = Path(base_dir)
        self.pos_dir     = self.base_dir / 'data' / 'positions'
        self.risk_dir    = self.base_dir / 'data' / 'risk'
        self.signals_dir = self.base_dir / 'data' / 'signals'
        self.eda_dir     = self.base_dir / 'data' / 'eda'
        self.fund_dir    = self.base_dir / 'data' / 'fundamental'
        self.tech_dir    = self.base_dir / 'data' / 'technical'
        self.port_dir    = self.base_dir / 'data' / 'portfolio'
        self.log_dir     = self.base_dir / 'logs'
        self.config_path = self.base_dir / 'config.yaml'

        for d in [self.port_dir, self.log_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._log_lines: list[str] = []

    # ──────────────────────────────────────────────────────────────────────────
    # Loaders
    # ──────────────────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self._log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _r(self, v, d: int = 2):
        if v is None: return None
        try:
            f = float(v)
            return None if (np.isnan(f) or np.isinf(f)) else round(f, d)
        except (TypeError, ValueError):
            return None

    def _load_config(self) -> dict:
        with open(self.config_path) as f:
            return yaml.safe_load(f)

    def _load_json(self, path: Path) -> dict | None:
        return json.load(open(path)) if path.exists() else None

    def _load_all_positions(self) -> list[dict]:
        return [
            json.load(open(f))
            for f in sorted(self.pos_dir.glob('*_position.json'))
        ]

    def _load_corr_matrix(self) -> pd.DataFrame | None:
        cross = self._load_json(self.eda_dir / 'cross_ticker_eda.json')
        if not cross:
            return None
        return pd.DataFrame(cross['return_correlation'])

    # ──────────────────────────────────────────────────────────────────────────
    # Position ranking and selection
    # ──────────────────────────────────────────────────────────────────────────

    def _enrich(self, pos: dict) -> dict:
        """Attach signal, fundamental, technical data to a position record."""
        ticker = pos['ticker']
        sig  = self._load_json(self.signals_dir / f"{ticker}_signal.json") or {}
        fund = self._load_json(self.fund_dir    / f"{ticker}_fundamental.json") or {}
        tech = self._load_json(self.tech_dir    / f"{ticker}_technical.json") or {}
        eda  = self._load_json(self.eda_dir     / f"{ticker}_eda.json") or {}

        pos['_signal']     = sig
        pos['_fund']       = fund
        pos['_tech']       = tech
        pos['_eda']        = eda
        pos['_combined']   = sig.get('combined_score', 0)
        pos['_conviction'] = sig.get('conviction')
        pos['_sig_type']   = sig.get('signal', 'NO_TRADE')
        pos['_ann_vol']    = (eda.get('return_stats') or {}).get('annualised_volatility_pct', 25.0)
        pos['_ann_ret']    = (eda.get('return_stats') or {}).get('annualised_return_pct', 0.0)
        return pos

    def _rank_key(self, pos: dict):
        return (
            PRIORITY_MAP.get(pos['_sig_type'], 9),
            CONVICTION_MAP.get(pos['_conviction'], 9),
            -pos['_combined'],
        )

    def _select_positions(self, enriched: list[dict], cfg: dict) -> list[dict]:
        """
        Filter to positions with shares > 0, rank, then greedily include
        until max_open_positions or capital is exhausted.
        """
        capital      = cfg['portfolio']['total_capital']
        max_pos      = cfg['portfolio']['max_open_positions']
        max_sector   = cfg['portfolio']['max_sector_exposure']

        candidates = [p for p in enriched if (p.get('final') or {}).get('shares', 0) > 0]
        candidates.sort(key=self._rank_key)

        selected     = []
        cash_used    = 0.0
        sector_used: dict[str, float] = {}

        for p in candidates:
            fin    = p['final']
            sector = fin.get('sector', 'Unknown')
            val    = fin['position_value_inr']

            if len(selected) >= max_pos:
                p['_exclusion'] = f'max_positions ({max_pos}) reached'
                continue
            if cash_used + val > capital:
                p['_exclusion'] = 'insufficient capital'
                continue
            sec_used_frac = (sector_used.get(sector, 0) + val) / capital
            if sec_used_frac > max_sector:
                p['_exclusion'] = f'sector {sector} would breach {max_sector:.0%} limit'
                continue

            selected.append(p)
            cash_used += val
            sector_used[sector] = sector_used.get(sector, 0) + val

        return selected

    # ──────────────────────────────────────────────────────────────────────────
    # Portfolio metrics
    # ──────────────────────────────────────────────────────────────────────────

    def _portfolio_metrics(self, selected: list[dict],
                           capital: float,
                           corr: pd.DataFrame | None) -> dict:
        if not selected:
            return {}

        tickers   = [p['ticker'] for p in selected]
        weights   = np.array([p['final']['position_value_inr'] / capital for p in selected])
        vols      = np.array([p['_ann_vol'] / 100.0 for p in selected])
        rets      = np.array([p['_ann_ret'] / 100.0 for p in selected])
        betas     = np.array([(p['final'].get('beta') or 1.0) for p in selected])

        # Weighted expected return
        port_ret  = float(np.dot(weights, rets))

        # Portfolio volatility via covariance matrix
        if corr is not None:
            try:
                # Align correlation matrix to our tickers (may be partial)
                available = [t for t in tickers if t in corr.index]
                if len(available) >= 2:
                    c = corr.loc[available, available].values
                    w = np.array([weights[tickers.index(t)] for t in available])
                    v = np.array([vols[tickers.index(t)]    for t in available])
                    cov = np.outer(v, v) * c
                    port_var = float(w @ cov @ w)
                else:
                    port_var = float(np.dot(weights**2, vols**2))
            except Exception:
                port_var = float(np.dot(weights**2, vols**2))
        else:
            port_var = float(np.dot(weights**2, vols**2))

        port_vol  = float(np.sqrt(port_var))

        # Weighted average individual vol (for diversification ratio)
        avg_indiv_vol = float(np.dot(weights, vols))
        div_ratio     = avg_indiv_vol / port_vol if port_vol > 0 else 1.0

        # Sharpe (risk-free = 0)
        sharpe = port_ret / port_vol if port_vol > 0 else None

        # Beta
        port_beta = float(np.dot(weights, betas))

        # Total position vol (not weighted by capital — just sum individual daily VaR)
        total_var_inr = sum(
            (p.get('_risk') or {}).get('var_metrics', {}).get('position_var_inr') or 0
            for p in selected
        )

        return {
            'tickers':                    tickers,
            'weights_pct':                {t: round(w*100,2) for t,w in zip(tickers, weights)},
            'portfolio_expected_return_pct': self._r(port_ret * 100),
            'portfolio_volatility_pct':   self._r(port_vol * 100),
            'portfolio_sharpe':           self._r(sharpe, 3),
            'portfolio_beta':             self._r(port_beta, 3),
            'diversification_ratio':      self._r(div_ratio, 3),
            'total_position_var_inr':     self._r(total_var_inr),
            'note_correlation_used':      corr is not None,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Scenario analysis
    # ──────────────────────────────────────────────────────────────────────────

    def _scenarios(self, selected: list[dict], capital: float) -> dict:
        bear, bull_t1, bull_t2 = 0.0, 0.0, 0.0

        rows = []
        for p in selected:
            fin     = p['final']
            k       = p.get('kelly') or {}
            shares  = fin['shares']
            entry   = fin['entry_price']
            stop    = fin['stop_loss']
            t1      = fin['target_1']
            t2      = fin['target_2']
            wr      = k.get('win_rate') or 0.5

            loss    = shares * (stop - entry)    # negative
            gain_t1 = shares * (t1   - entry)    # positive
            gain_t2 = shares * (t2   - entry)    # positive
            ev      = wr * gain_t1 + (1 - wr) * loss  # expected value vs T1

            bear    += loss
            bull_t1 += gain_t1
            bull_t2 += gain_t2

            rows.append({
                'ticker':       p['ticker'],
                'shares':       shares,
                'bear_pnl':     self._r(loss),
                'bull_t1_pnl':  self._r(gain_t1),
                'bull_t2_pnl':  self._r(gain_t2),
                'expected_value':self._r(ev),
                'win_rate_used': self._r(wr, 4),
            })

        return {
            'per_position': rows,
            'portfolio': {
                'bear_total_pnl':     self._r(bear),
                'bear_pnl_pct':       self._r(bear / capital * 100),
                'bull_t1_total_pnl':  self._r(bull_t1),
                'bull_t1_pnl_pct':    self._r(bull_t1 / capital * 100),
                'bull_t2_total_pnl':  self._r(bull_t2),
                'bull_t2_pnl_pct':    self._r(bull_t2 / capital * 100),
                'total_expected_value': self._r(sum(r['expected_value'] for r in rows)),
            },
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Order generation
    # ──────────────────────────────────────────────────────────────────────────

    def _build_orders(self, selected: list[dict]) -> list[dict]:
        orders = []
        for rank, p in enumerate(selected, 1):
            fin    = p['final']
            ticker = p['ticker']
            sig    = p['_signal']
            fund   = p['_fund']
            tech   = p['_tech']

            patterns  = sig.get('patterns', [])
            pat_str   = ', '.join(patterns) if patterns else 'combined score'
            rationale = (
                f"{p['_sig_type']} (combined={p['_combined']}/10): "
                f"tech={tech.get('technical_score')}/10 [{tech.get('signal')}], "
                f"fund={fund.get('fundamental_score')}/10, "
                f"patterns=[{pat_str}]"
            )

            orders.append({
                'priority':       rank,
                'ticker_nse':     f"{ticker}.NS",
                'ticker_clean':   ticker,
                'exchange':       'NSE',
                'action':         'BUY',
                'order_type':     'LIMIT',
                'validity':       'DAY',
                'quantity':       fin['shares'],
                'limit_price':    fin['entry_price'],
                'stop_loss':      fin['stop_loss'],
                'target_1':       fin['target_1'],
                'target_2':       fin['target_2'],
                'risk_reward':    fin['risk_reward'],
                'position_value': fin['position_value_inr'],
                'capital_at_risk':fin['capital_at_risk_inr'],
                'signal':         p['_sig_type'],
                'conviction':     p['_conviction'],
                'sector':         fin.get('sector'),
                'rationale':      rationale,
            })
        return orders

    # ──────────────────────────────────────────────────────────────────────────
    # Capital allocation table
    # ──────────────────────────────────────────────────────────────────────────

    def _capital_table(self, selected: list[dict], capital: float,
                        cfg: dict) -> dict:
        max_sector = cfg['portfolio']['max_sector_exposure']

        total_deployed = sum(p['final']['position_value_inr'] for p in selected)
        total_risk     = sum(p['final']['capital_at_risk_inr'] for p in selected)
        cash           = capital - total_deployed

        sector_map: dict[str, float] = {}
        for p in selected:
            s = p['final'].get('sector', 'Unknown')
            sector_map[s] = sector_map.get(s, 0) + p['final']['position_value_inr']

        return {
            'capital':           capital,
            'total_deployed':    round(total_deployed, 2),
            'deployed_pct':      round(total_deployed / capital * 100, 2),
            'remaining_cash':    round(cash, 2),
            'cash_pct':          round(cash / capital * 100, 2),
            'total_risk':        round(total_risk, 2),
            'risk_pct':          round(total_risk / capital * 100, 3),
            'sector_allocation': {
                s: {
                    'value':   round(v, 2),
                    'pct':     round(v / capital * 100, 2),
                    'breach':  (v / capital) > max_sector,
                }
                for s, v in sector_map.items()
            },
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        start_time      = time.time()
        self._log_lines = []

        cfg     = self._load_config()
        capital = cfg['portfolio']['total_capital']
        corr    = self._load_corr_matrix()

        all_pos = self._load_all_positions()
        if not all_pos:
            print("PortfolioManagerAgent: no position files in data/positions/")
            return {'succeeded': [], 'failed': [], 'output_paths': [], 'elapsed_s': 0.0}

        self._log(f"PortfolioManagerAgent — {len(all_pos)} positions, capital=₹{capital:,}")
        print(f"{'='*68}")
        print(f"PortfolioManagerAgent.run() — {len(all_pos)} candidates | ₹{capital:,}")
        print(f"{'='*68}")

        # Enrich, rank, select
        enriched = [self._enrich(p) for p in all_pos]

        # attach risk file for VaR lookup in metrics
        for p in enriched:
            p['_risk'] = self._load_json(self.risk_dir / f"{p['ticker']}_risk.json") or {}

        selected = self._select_positions(enriched, cfg)

        self._log(f"Selected {len(selected)}/{len(all_pos)} positions")
        print(f"\nPosition ranking & selection:")
        for p in sorted(enriched, key=self._rank_key):
            fin = (p.get('final') or {})
            shares = fin.get('shares', 0)
            excl = p.get('_exclusion', '')
            icon = '✅' if p in selected else ('⏭ ' if shares == 0 else '❌')
            print(
                f"  {icon} [{PRIORITY_MAP.get(p['_sig_type'],9)}.{CONVICTION_MAP.get(p['_conviction'],9)}]"
                f"  {p['ticker']:<14}  {p['_sig_type']:<12}  combined={p['_combined']:.1f}"
                f"  shares={shares}  {excl}"
            )

        # Metrics
        metrics  = self._portfolio_metrics(selected, capital, corr)
        scenarios= self._scenarios(selected, capital)
        orders   = self._build_orders(selected)
        cap_tbl  = self._capital_table(selected, capital, cfg)

        # ── Console output ────────────────────────────────────────────────────
        print(f"\n── Portfolio metrics ────────────────────────────────────────")
        if metrics:
            print(f"  Expected return     : {metrics['portfolio_expected_return_pct']:+.1f}% p.a.")
            print(f"  Portfolio volatility: {metrics['portfolio_volatility_pct']:.1f}% p.a.")
            print(f"  Portfolio Sharpe    : {metrics['portfolio_sharpe']}")
            print(f"  Portfolio beta      : {metrics['portfolio_beta']}")
            print(f"  Diversification ratio: {metrics['diversification_ratio']}  "
                  f"({'corr-adjusted' if metrics['note_correlation_used'] else 'uncorrelated'})")
            print(f"  Weights             : {metrics['weights_pct']}")

        print(f"\n── Scenario analysis ────────────────────────────────────────")
        sc  = scenarios['portfolio']
        print(f"  {'Scenario':<22} {'P&L':>10}  {'% of capital':>13}")
        print(f"  {'─'*48}")
        print(f"  {'Bear (all SL hit)':<22} ₹{sc['bear_total_pnl']:>9,.0f}  {sc['bear_pnl_pct']:>12.2f}%")
        print(f"  {'Bull T1 (all hit)':<22} ₹{sc['bull_t1_total_pnl']:>9,.0f}  {sc['bull_t1_pnl_pct']:>12.2f}%")
        print(f"  {'Bull T2 (all hit)':<22} ₹{sc['bull_t2_total_pnl']:>9,.0f}  {sc['bull_t2_pnl_pct']:>12.2f}%")
        print(f"  {'Expected value':<22} ₹{sc['total_expected_value']:>9,.0f}")
        print(f"\n  Per-position:")
        print(f"  {'Ticker':<14} {'Bear':>8}  {'T1':>8}  {'T2':>8}  {'EV':>8}  Win%")
        print(f"  {'─'*56}")
        for r in scenarios['per_position']:
            print(f"  {r['ticker']:<14} ₹{r['bear_pnl']:>7,.0f}  ₹{r['bull_t1_pnl']:>7,.0f}  "
                  f"₹{r['bull_t2_pnl']:>7,.0f}  ₹{r['expected_value']:>7,.0f}  {r['win_rate_used']:.1%}")

        print(f"\n── Capital allocation ───────────────────────────────────────")
        ct = cap_tbl
        print(f"  Deployed  ₹{ct['total_deployed']:>10,.0f}  ({ct['deployed_pct']:.1f}%)")
        print(f"  Cash      ₹{ct['remaining_cash']:>10,.0f}  ({ct['cash_pct']:.1f}%)")
        print(f"  At risk   ₹{ct['total_risk']:>10,.0f}  ({ct['risk_pct']:.2f}%)")
        for s, sv in ct['sector_allocation'].items():
            br = ' ⚠ BREACH' if sv['breach'] else ''
            print(f"  {s:<28} ₹{sv['value']:>9,.0f}  ({sv['pct']:.1f}%){br}")

        print(f"\n── Trade orders ─────────────────────────────────────────────")
        print(f"  {'P':<3} {'Ticker':<16} {'Qty':>4}  {'Limit':>8}  {'SL':>8}  {'T1':>8}  {'T2':>8}  {'R/R':>4}  Signal")
        print(f"  {'─'*75}")
        for o in orders:
            print(
                f"  {o['priority']:<3} {o['ticker_nse']:<16} {o['quantity']:>4}"
                f"  ₹{o['limit_price']:>7.2f}  ₹{o['stop_loss']:>7.2f}"
                f"  ₹{o['target_1']:>7.2f}  ₹{o['target_2']:>7.2f}"
                f"  {o['risk_reward']}:1  {o['signal']}"
            )

        # ── Write outputs ──────────────────────────────────────────────────────
        elapsed     = time.time() - start_time
        out_paths   = []

        state = {
            'run_at':          datetime.now().isoformat(),
            'capital':         capital,
            'selected_count':  len(selected),
            'candidates':      len(all_pos),
            'capital_table':   cap_tbl,
            'positions': [
                {
                    'ticker':          p['ticker'],
                    'signal':          p['_sig_type'],
                    'conviction':      p['_conviction'],
                    'combined_score':  p['_combined'],
                    'fund_score':      p['_fund'].get('fundamental_score'),
                    'tech_score':      p['_tech'].get('technical_score'),
                    'risk_decision':   p.get('risk_decision'),
                    'sizing_basis':    p.get('sizing_basis'),
                    'final':           p.get('final'),
                    'kelly':           {
                        k: v for k, v in (p.get('kelly') or {}).items()
                        if k in ('kelly_hist','kelly_trade','kelly_fraction',
                                 'kelly_applied_pct','kelly_shares','win_rate','has_edge')
                    },
                }
                for p in selected
            ],
        }
        for fname, data in [
            ('portfolio_state.json',   state),
            ('trade_orders.json',      {'run_at': state['run_at'], 'orders': orders}),
            ('portfolio_metrics.json', {'run_at': state['run_at'], **metrics}),
            ('scenario_analysis.json', {'run_at': state['run_at'], **scenarios}),
        ]:
            path = self.port_dir / fname
            with open(path, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            out_paths.append(str(path))

        log_path = self.log_dir / f"portfolio_{datetime.now().strftime('%Y-%m-%d')}.txt"
        self._log(f"Done — {elapsed:.1f}s  selected={len(selected)}")
        with open(log_path, 'w') as f:
            f.write('\n'.join([
                'SwingTradeIQ PortfolioManagerAgent',
                f"Date    : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"Duration: {elapsed:.1f}s",
                '',
                *self._log_lines,
            ]))

        print(f"\n{'='*68}")
        print(f"Done in {elapsed:.1f}s | {len(selected)} positions selected | {len(orders)} orders generated")
        print(f"{'='*68}")

        return {
            'succeeded':        [p['ticker'] for p in all_pos],
            'failed':           [],
            'selected_tickers': [p['ticker'] for p in selected],
            'orders':           orders,
            'output_paths':     out_paths,
            'elapsed_s':        round(elapsed, 1),
        }
