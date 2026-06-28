# SwingTradeIQ — Agent 8/10: PositionSizerAgent
# Built in Session 8.
# Handoff: reads data/risk/ + data/validated/ + config.yaml
#          → writes data/positions/

import time, json
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import yaml

MAX_POSITION_PCT    = 0.20   # hard cap: single position ≤ 20% of capital
MIN_KELLY_FLOOR     = 0.05   # if trade Kelly is positive but tiny, use this floor
NEGATIVE_KELLY_MULT = 0.50   # when trade Kelly ≤ 0, take 50% of risk-budget shares


class PositionSizerAgent:
    """
    Agent 8/10 — SwingTradeIQ Position Sizer Agent.

    Takes every trade approved by RiskAgent and computes the definitive
    share count using a two-constraint system:

        1. Kelly constraint  — fractional Kelly applied to the TRADE setup
        2. Risk-budget constraint — inherited from RiskAgent (2% max loss rule)

    The binding constraint (whichever gives fewer shares) is used.

    Kelly formula used (trade Kelly, not historical Kelly)
    -------------------------------------------------------
    Raw Kelly % of capital = win_rate − (1 − win_rate) / rr_ratio

    where:
      win_rate  = fraction of days with positive close-to-close return
                  over the validated history (best available proxy for
                  swing trade success probability at this R/R ratio)
      rr_ratio  = trade R/R from IndicatorEngine (fixed at 2.0 for all
                  ATR-based setups)

    Note: historical Kelly (based on avg_win / avg_loss of daily returns)
    is also computed and stored for reference. When a stock is in a
    downtrend, historical Kelly is negative; trade Kelly can still be
    positive if win_rate > 1/(1 + rr_ratio) — i.e. > 33% for a 2:1 trade.

    Fractional Kelly
    ----------------
    Applied Kelly = max(floor, raw_kelly) × kelly_fraction
    kelly_fraction is read from config.yaml (default 0.5 = half-Kelly).

    When trade Kelly ≤ 0: position sized at NEGATIVE_KELLY_MULT (50%) of
    the risk-budget shares — still allows a speculative trade but smaller.

    Portfolio allocation
    --------------------
    After sizing all positions, the agent checks:
      • Total capital deployed ≤ portfolio capital
      • No single position breaches MAX_POSITION_PCT (20%)
      • Total risk deployed across all positions is reported

    Outputs
    -------
        data/positions/{TICKER}_position.json  — per-ticker final sizing
        data/positions/positions_summary.json  — portfolio allocation view
        logs/position_{DATE}.txt               — run log
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir      = Path(base_dir)
        self.risk_dir      = self.base_dir / 'data' / 'risk'
        self.validated_dir = self.base_dir / 'data' / 'validated'
        self.positions_dir = self.base_dir / 'data' / 'positions'
        self.log_dir       = self.base_dir / 'logs'
        self.config_path   = self.base_dir / 'config.yaml'

        for d in [self.positions_dir, self.log_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._log_lines: list[str] = []

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
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

    def _load_risk(self, ticker: str) -> dict | None:
        p = self.risk_dir / f"{ticker}_risk.json"
        return json.load(open(p)) if p.exists() else None

    def _daily_returns(self, ticker: str) -> pd.Series | None:
        p = self.validated_dir / f"{ticker}_validated.csv"
        if not p.exists():
            return None
        df = pd.read_csv(p, index_col='Date', parse_dates=True)
        return df['Close'].pct_change().dropna()

    # ──────────────────────────────────────────────────────────────────────────
    # Kelly calculations
    # ──────────────────────────────────────────────────────────────────────────

    def _kelly_stats(self, returns: pd.Series, rr_ratio: float) -> dict:
        """
        Returns both historical Kelly (based on avg_win/avg_loss ratio of daily
        returns) and trade Kelly (based on win_rate and the trade's R/R ratio).
        """
        pos = returns[returns > 0]
        neg = returns[returns < 0]
        n   = len(returns)

        win_rate  = len(pos) / n if n > 0 else 0.5
        avg_win   = float(pos.mean())  if len(pos) > 0 else 0.0
        avg_loss  = float(abs(neg.mean())) if len(neg) > 0 else 0.0

        # Historical Kelly: uses empirical avg_win/avg_loss as the odds ratio
        if avg_loss > 0:
            hist_b       = avg_win / avg_loss
            kelly_hist   = win_rate - (1 - win_rate) / hist_b
        else:
            kelly_hist   = 0.0

        # Trade Kelly: uses the trade R/R ratio as the odds ratio
        # Positive when win_rate > 1/(1+rr_ratio) — the "edge" threshold
        kelly_trade = win_rate - (1 - win_rate) / rr_ratio
        edge_threshold = 1.0 / (1.0 + rr_ratio)  # min win_rate needed for edge

        return {
            'n_days':           n,
            'win_rate':         self._r(win_rate, 4),
            'loss_rate':        self._r(1 - win_rate, 4),
            'avg_win_pct':      self._r(avg_win * 100, 3),
            'avg_loss_pct':     self._r(avg_loss * 100, 3),
            'hist_odds_ratio':  self._r(avg_win / avg_loss if avg_loss > 0 else None, 4),
            'trade_rr_ratio':   rr_ratio,
            'edge_threshold':   self._r(edge_threshold, 4),
            'has_edge':         win_rate > edge_threshold,
            'kelly_hist':       self._r(kelly_hist, 4),
            'kelly_trade':      self._r(kelly_trade, 4),
        }

    def _apply_fractional_kelly(self, kelly_trade: float,
                                  kelly_fraction: float,
                                  capital: float,
                                  entry: float) -> tuple[float, int, str]:
        """
        Returns (kelly_capital_inr, kelly_shares, basis).
        """
        if kelly_trade <= 0:
            return 0.0, 0, 'negative_kelly'

        # Apply floor so tiny edges don't produce single-digit positions
        effective_kelly = max(kelly_trade, MIN_KELLY_FLOOR) * kelly_fraction
        kelly_capital   = effective_kelly * capital
        kelly_capital   = min(kelly_capital, capital * MAX_POSITION_PCT)
        kelly_shares    = int(kelly_capital / entry)

        return self._r(kelly_capital), kelly_shares, 'kelly_constrained'

    # ──────────────────────────────────────────────────────────────────────────
    # Per-ticker sizing
    # ──────────────────────────────────────────────────────────────────────────

    def _size_one(self, ticker: str, cfg: dict) -> dict:
        capital        = cfg['portfolio']['total_capital']
        kelly_fraction = cfg['kelly']['fraction']

        risk = self._load_risk(ticker)
        if not risk:
            return {'ticker': ticker, 'error': 'no risk file'}

        decision = risk.get('risk_decision', '')
        if decision == 'REJECTED':
            result = {
                'ticker':         ticker,
                'signal':         risk.get('signal'),
                'risk_decision':  decision,
                'final_shares':   0,
                'position_value': 0.0,
                'sizing_basis':   'rejected',
                'kelly':          None,
            }
            self._save(ticker, result)
            return result

        tl    = risk.get('trade_levels', {}) or {}
        sz    = risk.get('position_sizing', {}) or {}
        entry = tl.get('entry')
        stop  = tl.get('stop_loss')
        rr    = tl.get('risk_reward_1') or 2.0

        if not entry or not stop:
            return {'ticker': ticker, 'error': 'missing entry/stop in risk file'}

        # ── Kelly stats from historical returns ───────────────────────────────
        returns = self._daily_returns(ticker)
        if returns is None or len(returns) < 50:
            kelly_stats = {}
            kelly_trade = 0.0
        else:
            kelly_stats = self._kelly_stats(returns, float(rr))
            kelly_trade = float(kelly_stats['kelly_trade'])

        # ── Kelly-based shares ────────────────────────────────────────────────
        kelly_capital, kelly_shares, kelly_basis = self._apply_fractional_kelly(
            kelly_trade, kelly_fraction, capital, entry
        )

        # ── Risk-budget shares (from RiskAgent) ───────────────────────────────
        rb_shares = sz.get('shares', 0)
        rb_value  = sz.get('position_value_inr', 0.0)
        rb_mult   = sz.get('size_multiplier', 1.0)

        # ── Determine final shares and binding constraint ─────────────────────
        if kelly_trade <= 0:
            # No statistical edge: take conservative fraction of risk-budget
            final_shares  = max(1, int(rb_shares * NEGATIVE_KELLY_MULT))
            sizing_basis  = 'minimum_viable'
            binding       = 'negative_kelly'
        else:
            final_shares  = min(kelly_shares, rb_shares)
            if final_shares == kelly_shares < rb_shares:
                binding = 'kelly'
                sizing_basis = 'kelly_constrained'
            elif final_shares == rb_shares <= kelly_shares:
                binding = 'risk_budget'
                sizing_basis = 'risk_budget_constrained'
            else:
                binding = 'both_equal'
                sizing_basis = 'both_constraints_equal'

        # Hard cap: no position > MAX_POSITION_PCT
        cap_shares   = int(capital * MAX_POSITION_PCT / entry)
        if final_shares > cap_shares:
            final_shares = cap_shares
            binding      = 'capital_cap'

        final_shares = max(0, final_shares)
        pos_value    = round(final_shares * entry, 2)
        pos_pct      = round(pos_value / capital * 100, 2)
        risk_amount  = round(final_shares * (entry - stop), 2)
        risk_pct     = round(risk_amount / capital * 100, 3)

        result = {
            'ticker':           ticker,
            'signal':           risk.get('signal'),
            'risk_decision':    decision,
            'sizing_basis':     sizing_basis,
            'binding_constraint': binding,
            'kelly': {
                **kelly_stats,
                'kelly_fraction':      kelly_fraction,
                'kelly_capital_inr':   kelly_capital,
                'kelly_shares':        kelly_shares,
                'kelly_applied_pct':   self._r(
                    max(kelly_trade, MIN_KELLY_FLOOR) * kelly_fraction * 100
                    if kelly_trade > 0 else 0.0
                ),
            } if kelly_stats else {'kelly_trade': kelly_trade, 'kelly_fraction': kelly_fraction},
            'risk_budget': {
                'shares':            rb_shares,
                'position_value':    rb_value,
                'size_multiplier':   rb_mult,
            },
            'final': {
                'shares':               final_shares,
                'entry_price':          self._r(entry),
                'stop_loss':            self._r(stop),
                'position_value_inr':   pos_value,
                'position_pct_capital': pos_pct,
                'capital_at_risk_inr':  risk_amount,
                'capital_at_risk_pct':  risk_pct,
                'target_1':             self._r(tl.get('target_1')),
                'target_2':             self._r(tl.get('target_2')),
                'risk_reward':          self._r(rr, 1),
                'sector':               (risk.get('market_risk') or {}).get('sector'),
                'beta':                 (risk.get('market_risk') or {}).get('beta'),
            },
        }

        self._save(ticker, result)
        return result

    def _save(self, ticker: str, data: dict) -> None:
        out = self.positions_dir / f"{ticker}_position.json"
        with open(out, 'w') as f:
            json.dump(data, f, indent=2, default=str)

    # ──────────────────────────────────────────────────────────────────────────
    # Portfolio-level allocation check
    # ──────────────────────────────────────────────────────────────────────────

    def _portfolio_summary(self, positions: list[dict], cfg: dict) -> dict:
        capital    = cfg['portfolio']['total_capital']
        max_sector = cfg['portfolio']['max_sector_exposure']

        active = [p for p in positions
                  if p.get('final', {}).get('shares', 0) > 0]

        total_deployed = sum(p['final']['position_value_inr'] for p in active)
        total_risk     = sum(p['final']['capital_at_risk_inr'] for p in active)
        remaining_cash = capital - total_deployed
        utilisation    = round(total_deployed / capital * 100, 2)

        # Beta-weighted exposure
        bw_exposure = sum(
            (p['final'].get('beta') or 1.0) * p['final']['position_value_inr']
            for p in active
        )

        # Sector breakdown
        sector_map: dict[str, dict] = {}
        for p in active:
            s = p['final'].get('sector', 'Unknown')
            sector_map.setdefault(s, {'value': 0.0, 'tickers': []})
            sector_map[s]['value']   += p['final']['position_value_inr']
            sector_map[s]['tickers'].append(p['ticker'])

        sector_pct = {
            s: {'pct': round(v['value'] / capital * 100, 2), 'tickers': v['tickers']}
            for s, v in sector_map.items()
        }
        sector_breaches = {s: v for s, v in sector_pct.items()
                           if v['pct'] > max_sector * 100}

        return {
            'capital':                capital,
            'total_deployed_inr':     round(total_deployed, 2),
            'total_deployed_pct':     round(total_deployed / capital * 100, 2),
            'remaining_cash_inr':     round(remaining_cash, 2),
            'portfolio_utilisation_pct': utilisation,
            'total_risk_inr':         round(total_risk, 2),
            'total_risk_pct':         round(total_risk / capital * 100, 3),
            'beta_weighted_exposure': round(bw_exposure, 2),
            'active_positions':       len(active),
            'sector_breakdown':       sector_pct,
            'sector_breaches':        sector_breaches,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        start_time      = time.time()
        self._log_lines = []

        cfg         = self._load_config()
        risk_files  = sorted(self.risk_dir.glob('*_risk.json'))
        tickers     = [f.stem.replace('_risk', '') for f in risk_files]

        if not tickers:
            print("PositionSizerAgent: no risk files in data/risk/")
            return {'succeeded': [], 'failed': [], 'output_paths': [], 'elapsed_s': 0.0}

        capital = cfg['portfolio']['total_capital']
        kf      = cfg['kelly']['fraction']
        self._log(f"PositionSizerAgent — {len(tickers)} tickers  capital=₹{capital:,}  kelly_fraction={kf}")
        print(f"{'='*72}")
        print(f"PositionSizerAgent.run() — {len(tickers)} tickers | capital=₹{capital:,} | kelly={kf}×")
        print(f"{'='*72}")

        positions = []
        succeeded = []
        failed    = []
        out_paths = []

        for i, ticker in enumerate(tickers, 1):
            print(f"[{i:>3}/{len(tickers)}] {ticker:<16}", end='', flush=True)
            try:
                pos = self._size_one(ticker, cfg)
                positions.append(pos)
                succeeded.append(ticker)
                out_paths.append(str(self.positions_dir / f"{ticker}_position.json"))

                fin = pos.get('final', {})
                k   = pos.get('kelly') or {}

                if fin.get('shares', 0) == 0:
                    print(f"  ⏭  SKIPPED ({pos.get('sizing_basis','—')})")
                    continue

                kt  = k.get('kelly_trade')
                ksh = k.get('kelly_shares', '—')
                print(
                    f"  ✅ shares={fin['shares']:<4}  pos=₹{fin['position_value_inr']:>10,.0f}"
                    f"  risk=₹{fin['capital_at_risk_inr']:>7,.0f} ({fin['capital_at_risk_pct']:.2f}%)"
                    f"  kelly_trade={kt}  kelly_sh={ksh}"
                    f"  binding={pos.get('binding_constraint','—')}"
                )
                self._log(f"{ticker}: shares={fin['shares']} pos=₹{fin['position_value_inr']} "
                          f"binding={pos.get('binding_constraint')} kelly_trade={kt}")
            except Exception as exc:
                import traceback
                failed.append({'ticker': ticker, 'reason': str(exc)})
                print(f"  ❌ {exc}")
                self._log(f"ERROR {ticker}: {traceback.format_exc()}")

        # ── Portfolio summary ─────────────────────────────────────────────────
        portfolio = self._portfolio_summary(positions, cfg)

        # ── Ranked table ──────────────────────────────────────────────────────
        active = sorted(
            [p for p in positions if p.get('final', {}).get('shares', 0) > 0],
            key=lambda p: p['final']['position_value_inr'],
            reverse=True,
        )

        print(f"\n{'─'*72}")
        print(f"{'#':<3} {'Ticker':<14} {'Signal':<12} {'Shares':>6}  {'Position ₹':>12}  "
              f"{'Risk ₹':>8}  {'Risk%':>5}  {'Basis'}")
        print(f"{'─'*72}")
        for i, p in enumerate(active, 1):
            f = p['final']
            print(
                f"{i:<3} {p['ticker']:<14} {p.get('signal','—'):<12}"
                f" {f['shares']:>6}  {f['position_value_inr']:>12,.0f}"
                f"  {f['capital_at_risk_inr']:>8,.0f}  {f['capital_at_risk_pct']:>5.2f}%"
                f"  {p['sizing_basis']}"
            )

        print(f"\n── Portfolio allocation ─────────────────────────────────────")
        pf = portfolio
        print(f"  Active positions : {pf['active_positions']}")
        print(f"  Capital deployed : ₹{pf['total_deployed_inr']:>10,.0f}  ({pf['total_deployed_pct']:.1f}%)")
        print(f"  Remaining cash   : ₹{pf['remaining_cash_inr']:>10,.0f}  ({100-pf['total_deployed_pct']:.1f}%)")
        print(f"  Total risk       : ₹{pf['total_risk_inr']:>10,.0f}  ({pf['total_risk_pct']:.2f}%)")
        print(f"  Beta-wt exposure : ₹{pf['beta_weighted_exposure']:>10,.0f}")
        print(f"  Sector breakdown :")
        for sector, sv in pf['sector_breakdown'].items():
            breach = ' ⚠ BREACH' if sector in pf['sector_breaches'] else ''
            print(f"    {sector:<28} {sv['pct']:>5.1f}%  {sv['tickers']}{breach}")

        # ── Write summary ─────────────────────────────────────────────────────
        elapsed      = time.time() - start_time
        summary_data = {
            'run_at':    datetime.now().isoformat(),
            'portfolio': portfolio,
            'positions': [
                {
                    'ticker':        p['ticker'],
                    'signal':        p.get('signal'),
                    'risk_decision': p.get('risk_decision'),
                    'sizing_basis':  p.get('sizing_basis'),
                    'binding':       p.get('binding_constraint'),
                    'kelly_trade':   (p.get('kelly') or {}).get('kelly_trade'),
                    'kelly_shares':  (p.get('kelly') or {}).get('kelly_shares'),
                    'final_shares':  (p.get('final') or {}).get('shares', 0),
                    'position_value':(p.get('final') or {}).get('position_value_inr', 0),
                    'risk_inr':      (p.get('final') or {}).get('capital_at_risk_inr', 0),
                    'risk_pct':      (p.get('final') or {}).get('capital_at_risk_pct', 0),
                    'sector':        (p.get('final') or {}).get('sector'),
                }
                for p in positions
            ],
        }
        summary_path = self.positions_dir / 'positions_summary.json'
        with open(summary_path, 'w') as f:
            json.dump(summary_data, f, indent=2, default=str)
        out_paths.append(str(summary_path))

        log_path = self.log_dir / f"position_{datetime.now().strftime('%Y-%m-%d')}.txt"
        self._log(f"Done — {elapsed:.1f}s")
        with open(log_path, 'w') as f:
            f.write('\n'.join([
                'SwingTradeIQ PositionSizerAgent',
                f"Date    : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"Duration: {elapsed:.1f}s",
                '',
                *self._log_lines,
            ]))

        print(f"\n{'='*72}")
        print(f"Done in {elapsed:.1f}s | ✅ {len(succeeded)} sized | ❌ {len(failed)} errors")
        print(f"{'='*72}")

        return {
            'succeeded':    succeeded,
            'failed':       failed,
            'output_paths': out_paths,
            'elapsed_s':    round(elapsed, 1),
            'active_tickers': [p['ticker'] for p in active],
            'portfolio':    portfolio,
        }
