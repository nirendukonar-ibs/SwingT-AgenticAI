# SwingTradeIQ — Agent 7/10: RiskAgent
# Built in Session 7.
# Handoff: reads data/signals/ + data/eda/ + data/fundamental/ + data/validated/
#          → writes data/risk/

import time, json
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import yaml

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_RR_RATIO      = 1.5   # minimum acceptable risk-reward ratio (hard rule)
MAX_DRAWDOWN_HARD = -50.0 # max historical drawdown % → hard reject below this
MAX_ATR_PCT_HARD  = 8.0   # ATR% above this → hard reject (too erratic)

MAX_DRAWDOWN_SOFT = -30.0 # max_dd worse than this → reduce size 25 %
MAX_ATR_PCT_SOFT  =  4.0  # ATR% above this → reduce size 25 %
NEGATIVE_SHARPE_REDUCTION = 0.25  # reduce by 25 % if Sharpe < 0

VAR_CONFIDENCE   = 0.95   # 95 % confidence for VaR / CVaR
MAX_POSITION_PCT = 0.20   # single position can't exceed 20 % of capital


class RiskAgent:
    """
    Agent 7/10 — SwingTradeIQ Risk Agent.

    Evaluates every actionable signal (SWING_BUY / WATCH) against a layered
    risk framework and decides whether a trade is APPROVED, APPROVED_REDUCED,
    or REJECTED. NO_TRADE signals are passed through as-is.

    Per-trade risk metrics
    ----------------------
    VaR 95 %      : historical simulation on 1-year daily returns
    CVaR 95 %     : expected shortfall (mean of returns < VaR threshold)
    Max position  : largest position where risk budget (2 % of capital)
                    is not breached, further capped at 20 % of capital
    Risk score    : composite 0–10 (lower = safer) across vol, drawdown,
                    tail risk, price volatility, and market beta

    Hard rejection rules
    --------------------
    1. Signal is NO_TRADE
    2. R/R ratio < 1.5
    3. Historical max drawdown worse than -50 %
    4. ATR % (daily price range / price) > 8 %

    Soft rules (trigger APPROVED_REDUCED)
    --------------------------------------
    A. Historical max drawdown < -30 %   → position × 0.75
    B. ATR % > 4 %                       → position × 0.75
    C. Sharpe ratio < 0                  → position × 0.75
    (rules stack multiplicatively)

    Portfolio-level checks (advisory, not blocking)
    ------------------------------------------------
    Sector concentration vs max_sector_exposure from config
    Beta-weighted portfolio exposure summary

    Outputs
    -------
        data/risk/{TICKER}_risk.json   — full per-ticker risk assessment
        data/risk/risk_summary.json    — portfolio-level view + ranking
        logs/risk_{DATE}.txt           — run log
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir      = Path(base_dir)
        self.signals_dir   = self.base_dir / 'data' / 'signals'
        self.eda_dir       = self.base_dir / 'data' / 'eda'
        self.fund_dir      = self.base_dir / 'data' / 'fundamental'
        self.validated_dir = self.base_dir / 'data' / 'validated'
        self.risk_dir      = self.base_dir / 'data' / 'risk'
        self.log_dir       = self.base_dir / 'logs'
        self.config_path   = self.base_dir / 'config.yaml'

        for d in [self.risk_dir, self.log_dir]:
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

    def _daily_returns(self, ticker: str) -> pd.Series | None:
        path = self.validated_dir / f"{ticker}_validated.csv"
        if not path.exists():
            return None
        df  = pd.read_csv(path, index_col='Date', parse_dates=True)
        ret = np.log(df['Close'] / df['Close'].shift(1)).dropna()
        return ret

    # ──────────────────────────────────────────────────────────────────────────
    # VaR / CVaR  (historical simulation)
    # ──────────────────────────────────────────────────────────────────────────

    def _var_metrics(self, returns: pd.Series, position_value: float) -> dict:
        sorted_ret = np.sort(returns.values)
        idx_var    = int(np.floor((1 - VAR_CONFIDENCE) * len(sorted_ret)))
        var_pct    = float(sorted_ret[idx_var])           # negative number
        cvar_pct   = float(sorted_ret[:idx_var + 1].mean()) if idx_var > 0 else var_pct

        return {
            'daily_var_95_pct':   self._r(var_pct * 100, 3),
            'daily_cvar_95_pct':  self._r(cvar_pct * 100, 3),
            'position_var_inr':   self._r(abs(var_pct)  * position_value),
            'position_cvar_inr':  self._r(abs(cvar_pct) * position_value),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Risk score  (0–10, lower = safer)
    # ──────────────────────────────────────────────────────────────────────────

    def _risk_score(self, ann_vol: float, max_dd: float,
                    skewness: float, kurtosis: float,
                    atr_pct: float, beta: float) -> tuple[float, dict]:

        def _vol_score(v):
            if v < 15: return 2.0
            if v < 20: return 4.0
            if v < 25: return 6.0
            if v < 30: return 7.0
            return 9.0

        def _dd_score(d):           # d is negative, e.g. -27.78
            if d > -10:  return 2.0
            if d > -20:  return 4.0
            if d > -30:  return 6.0
            if d > -40:  return 7.0
            return 9.0

        def _tail_score(skew, kurt):
            s = 5.0
            if skew < -0.5: s += 1.5   # left-skewed = bad for longs
            if skew > 0.3:  s -= 1.0
            if kurt > 5:    s += 2.0   # fat tails
            elif kurt > 3:  s += 1.0
            return max(0.0, min(10.0, s))

        def _atr_score(a):
            if a < 1.5: return 2.0
            if a < 2.5: return 4.0
            if a < 3.5: return 6.0
            if a < 5.0: return 7.0
            return 9.0

        def _beta_score(b):
            if b < 0.3:  return 2.0
            if b < 0.6:  return 4.0
            if b < 1.0:  return 6.0
            if b < 1.5:  return 7.0
            return 9.0

        components = {
            'volatility':  {'score': _vol_score(ann_vol),           'weight': 0.25},
            'drawdown':    {'score': _dd_score(max_dd),             'weight': 0.25},
            'tail_risk':   {'score': _tail_score(skewness, kurtosis),'weight': 0.20},
            'price_vol':   {'score': _atr_score(atr_pct),           'weight': 0.15},
            'market_beta': {'score': _beta_score(beta or 1.0),      'weight': 0.15},
        }
        composite = round(sum(v['score'] * v['weight'] for v in components.values()), 2)

        breakdown = {k: {'score': v['score'], 'weight': v['weight']}
                     for k, v in components.items()}
        return composite, breakdown

    # ──────────────────────────────────────────────────────────────────────────
    # Position sizing (pre-Kelly; full Kelly in PositionSizerAgent)
    # ──────────────────────────────────────────────────────────────────────────

    def _size_position(self, entry: float, stop: float,
                       capital: float, risk_per_trade: float,
                       size_multiplier: float) -> dict:
        risk_per_share = entry - stop
        if risk_per_share <= 0:
            return {'error': 'stop >= entry'}

        max_risk_inr    = capital * risk_per_trade * size_multiplier
        raw_shares      = int(max_risk_inr / risk_per_share)
        cap_by_pct      = int(capital * MAX_POSITION_PCT / entry)
        shares          = min(raw_shares, cap_by_pct)
        position_value  = round(shares * entry, 2)
        position_pct    = round(position_value / capital * 100, 2)

        return {
            'max_risk_budget_inr':     self._r(max_risk_inr),
            'risk_per_share':          self._r(risk_per_share),
            'shares':                  shares,
            'position_value_inr':      position_value,
            'position_pct_of_capital': position_pct,
            'size_multiplier':         self._r(size_multiplier, 3),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Per-ticker assessment
    # ──────────────────────────────────────────────────────────────────────────

    def _assess_one(self, ticker: str, cfg: dict) -> dict:
        capital       = cfg['portfolio']['total_capital']
        risk_per_trade= cfg['portfolio']['risk_per_trade']

        sig  = self._load_json(self.signals_dir   / f"{ticker}_signal.json")
        eda  = self._load_json(self.eda_dir        / f"{ticker}_eda.json")
        fund = self._load_json(self.fund_dir       / f"{ticker}_fundamental.json")

        if not sig or not eda or not fund:
            return {'ticker': ticker, 'risk_decision': 'ERROR',
                    'reason': 'missing upstream data'}

        signal      = sig['signal']
        trade_lvls  = sig.get('trade_levels') or {}

        # Pass NO_TRADE straight through
        if signal == 'NO_TRADE':
            result = {
                'ticker': ticker, 'signal': signal,
                'risk_decision': 'REJECTED',
                'rejection_reason': 'NO_TRADE signal from IndicatorEngine',
                'risk_flags': ['NO_TRADE'],
            }
            out = self.risk_dir / f"{ticker}_risk.json"
            with open(out, 'w') as f:
                json.dump(result, f, indent=2, default=str)
            return result

        # ── Pull stats ────────────────────────────────────────────────────────
        entry     = trade_lvls.get('entry')
        stop      = trade_lvls.get('stop_loss')
        rr        = trade_lvls.get('risk_reward_1')
        atr_pct   = sig['snapshot'].get('atr_pct') or 0.0
        ann_vol   = eda['return_stats']['annualised_volatility_pct']
        max_dd    = eda['drawdown']['max_drawdown_pct']
        skewness  = eda['return_stats']['skewness']
        kurtosis  = eda['return_stats']['kurtosis']
        sharpe    = eda['return_stats']['sharpe_ratio']
        beta      = fund['raw_fundamentals'].get('beta')
        sector    = fund.get('sector', 'Unknown')

        # ── Hard rejection checks ──────────────────────────────────────────────
        flags   : list[str] = []
        hard_rej: str | None = None

        if rr is not None and rr < MIN_RR_RATIO:
            hard_rej = f'R/R {rr} < minimum {MIN_RR_RATIO}'
            flags.append('RR_TOO_LOW')
        if max_dd < MAX_DRAWDOWN_HARD:
            hard_rej = f'max drawdown {max_dd}% worse than hard limit {MAX_DRAWDOWN_HARD}%'
            flags.append('DRAWDOWN_EXTREME')
        if atr_pct > MAX_ATR_PCT_HARD:
            hard_rej = f'ATR% {atr_pct}% exceeds hard limit {MAX_ATR_PCT_HARD}%'
            flags.append('ATR_EXTREME')
        if entry is None or stop is None:
            hard_rej = 'missing entry/stop levels'
            flags.append('NO_LEVELS')

        if hard_rej:
            result = {
                'ticker': ticker, 'signal': signal,
                'risk_decision': 'REJECTED',
                'rejection_reason': hard_rej,
                'risk_flags': flags,
                'market_risk': {
                    'sector': sector, 'beta': beta,
                    'ann_vol_pct': ann_vol, 'max_dd_pct': max_dd,
                    'sharpe_ratio': sharpe,
                },
            }
            out = self.risk_dir / f"{ticker}_risk.json"
            with open(out, 'w') as f:
                json.dump(result, f, indent=2, default=str)
            return result

        # ── Soft rules — build size multiplier ───────────────────────────────
        size_mult = 1.0
        soft_flags: list[str] = []

        if max_dd < MAX_DRAWDOWN_SOFT:
            size_mult *= 0.75
            soft_flags.append(f'DRAWDOWN_HIGH({max_dd:.1f}%→-25%size)')
        if atr_pct > MAX_ATR_PCT_SOFT:
            size_mult *= 0.75
            soft_flags.append(f'ATR_HIGH({atr_pct:.1f}%→-25%size)')
        if sharpe is not None and sharpe < 0:
            size_mult *= (1.0 - NEGATIVE_SHARPE_REDUCTION)
            soft_flags.append(f'NEGATIVE_SHARPE({sharpe:.2f}→-25%size)')

        flags.extend(soft_flags)
        decision = 'APPROVED_REDUCED' if soft_flags else 'APPROVED'

        # ── Position sizing ───────────────────────────────────────────────────
        sizing = self._size_position(entry, stop, capital, risk_per_trade, size_mult)

        # ── VaR / CVaR ────────────────────────────────────────────────────────
        returns  = self._daily_returns(ticker)
        pos_val  = sizing.get('position_value_inr', 0)
        var_dict = self._var_metrics(returns, pos_val) if returns is not None and pos_val > 0 else {}

        # ── Risk score ────────────────────────────────────────────────────────
        risk_score, score_breakdown = self._risk_score(
            ann_vol, max_dd, skewness, kurtosis, atr_pct, beta or 1.0
        )

        # ── Risk checks table (for transparency) ──────────────────────────────
        risk_checks = {
            'rr_ratio':     {'value': rr,      'threshold': MIN_RR_RATIO,     'pass': (rr or 0) >= MIN_RR_RATIO},
            'max_drawdown': {'value': max_dd,   'threshold': MAX_DRAWDOWN_HARD,'pass': max_dd >= MAX_DRAWDOWN_HARD},
            'atr_pct':      {'value': atr_pct,  'threshold': MAX_ATR_PCT_HARD, 'pass': atr_pct <= MAX_ATR_PCT_HARD},
            'sharpe_ratio': {'value': sharpe,   'threshold': 0,                'pass': (sharpe or 0) >= 0},
        }

        result = {
            'ticker':          ticker,
            'signal':          signal,
            'risk_decision':   decision,
            'risk_flags':      flags,
            'risk_score':      risk_score,
            'risk_score_breakdown': score_breakdown,
            'risk_checks':     risk_checks,
            'position_sizing': sizing,
            'var_metrics':     var_dict,
            'market_risk': {
                'sector':      sector,
                'beta':        beta,
                'ann_vol_pct': ann_vol,
                'max_dd_pct':  max_dd,
                'sharpe_ratio':sharpe,
                'skewness':    skewness,
                'kurtosis':    kurtosis,
            },
            'trade_levels':    trade_lvls,
        }

        out = self.risk_dir / f"{ticker}_risk.json"
        with open(out, 'w') as f:
            json.dump(result, f, indent=2, default=str)

        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Portfolio-level checks
    # ──────────────────────────────────────────────────────────────────────────

    def _portfolio_check(self, assessments: list[dict], cfg: dict) -> dict:
        capital       = cfg['portfolio']['total_capital']
        max_sector    = cfg['portfolio']['max_sector_exposure']

        approved = [a for a in assessments
                    if a.get('risk_decision') in ('APPROVED', 'APPROVED_REDUCED')]

        # Sector concentration
        sector_exposure: dict[str, float] = {}
        for a in approved:
            sector = a.get('market_risk', {}).get('sector', 'Unknown')
            pos_val = (a.get('position_sizing') or {}).get('position_value_inr', 0)
            sector_exposure[sector] = sector_exposure.get(sector, 0) + pos_val

        sector_pct = {s: round(v / capital * 100, 2) for s, v in sector_exposure.items()}
        sector_breaches = {s: p for s, p in sector_pct.items() if p > max_sector * 100}

        # Beta-weighted exposure
        total_beta_exposure = 0.0
        for a in approved:
            beta    = (a.get('market_risk') or {}).get('beta') or 1.0
            pos_val = (a.get('position_sizing') or {}).get('position_value_inr', 0)
            total_beta_exposure += beta * pos_val

        # Total risk deployed
        total_risk_inr  = sum(
            (a.get('position_sizing') or {}).get('max_risk_budget_inr', 0)
            for a in approved
        )
        total_risk_pct  = round(total_risk_inr / capital * 100, 2)

        return {
            'approved_count':         len(approved),
            'total_risk_deployed_inr':round(total_risk_inr, 2),
            'total_risk_deployed_pct':total_risk_pct,
            'beta_weighted_exposure': round(total_beta_exposure, 2),
            'sector_exposure_pct':    sector_pct,
            'sector_breaches':        sector_breaches,
            'max_sector_limit_pct':   max_sector * 100,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        start_time      = time.time()
        self._log_lines = []

        cfg         = self._load_config()
        sig_files   = sorted(self.signals_dir.glob('*_signal.json'))
        tickers     = [f.stem.replace('_signal', '') for f in sig_files]

        if not tickers:
            print("RiskAgent: no signal files found in data/signals/")
            return {'succeeded': [], 'failed': [], 'output_paths': [], 'elapsed_s': 0.0}

        capital = cfg['portfolio']['total_capital']
        self._log(f"RiskAgent — {len(tickers)} tickers  capital=₹{capital:,}")
        print(f"{'='*65}")
        print(f"RiskAgent.run() — {len(tickers)} tickers | capital=₹{capital:,} | risk/trade={cfg['portfolio']['risk_per_trade']:.0%}")
        print(f"{'='*65}")

        assessments = []
        succeeded   = []
        failed      = []
        out_paths   = []

        for i, ticker in enumerate(tickers, 1):
            print(f"[{i:>3}/{len(tickers)}] {ticker:<16}", end='', flush=True)
            try:
                result = self._assess_one(ticker, cfg)
                assessments.append(result)
                succeeded.append(ticker)
                out_paths.append(str(self.risk_dir / f"{ticker}_risk.json"))

                decision  = result.get('risk_decision', '?')
                rs        = result.get('risk_score')
                sizing    = result.get('position_sizing') or {}
                flags     = result.get('risk_flags', [])

                icon = {'APPROVED': '✅', 'APPROVED_REDUCED': '⚠ ', 'REJECTED': '❌'}.get(decision, '?')
                flag_str = f"  [{','.join(flags)}]" if flags else ''

                print(
                    f"  {icon} {decision:<18}"
                    f"  risk_score={rs}/10"
                    f"  shares={sizing.get('shares', '—')}"
                    f"  pos=₹{sizing.get('position_value_inr', '—')}"
                    f"{flag_str}"
                )
                self._log(f"{ticker}: {decision}  risk={rs}  flags={flags}")
            except Exception as exc:
                import traceback
                failed.append({'ticker': ticker, 'reason': str(exc)})
                print(f"  ❌ ERROR: {exc}")
                self._log(f"ERROR {ticker}: {traceback.format_exc()}")

        # ── Portfolio summary ──────────────────────────────────────────────────
        portfolio = self._portfolio_check(assessments, cfg)

        # ── Console summary table ──────────────────────────────────────────────
        ranking = sorted(
            [a for a in assessments if a.get('risk_decision') != 'ERROR'],
            key=lambda a: a.get('risk_score') or 99
        )

        print(f"\n{'─'*65}")
        print(f"{'Rank':<5} {'Ticker':<14} {'Decision':<20} {'RScore':>7}  {'Shares':>7}  {'Pos ₹':>10}  Flags")
        print(f"{'─'*65}")
        for i, a in enumerate(ranking, 1):
            sz = a.get('position_sizing') or {}
            print(
                f"{i:<5} {a['ticker']:<14} {a['risk_decision']:<20}"
                f" {str(a.get('risk_score', '—')):>7}"
                f"  {str(sz.get('shares', '—')):>7}"
                f"  {str(sz.get('position_value_inr', '—')):>10}"
                f"  {','.join(a.get('risk_flags', [])) or '—'}"
            )

        print(f"\n── Portfolio risk summary ────────────────────────────────")
        print(f"  Approved positions   : {portfolio['approved_count']}")
        print(f"  Total risk deployed  : ₹{portfolio['total_risk_deployed_inr']:,}  ({portfolio['total_risk_deployed_pct']}% of capital)")
        print(f"  Beta-weighted exposure: ₹{portfolio['beta_weighted_exposure']:,}")
        print(f"  Sector exposure      : {portfolio['sector_exposure_pct']}")
        if portfolio['sector_breaches']:
            print(f"  ⚠  SECTOR BREACH     : {portfolio['sector_breaches']}  (limit {portfolio['max_sector_limit_pct']}%)")

        # ── Write summary ──────────────────────────────────────────────────────
        elapsed = time.time() - start_time
        summary = {
            'run_at':        datetime.now().isoformat(),
            'capital':       capital,
            'total_tickers': len(tickers),
            'portfolio':     portfolio,
            'ranking': [
                {
                    'ticker':          a['ticker'],
                    'signal':          a.get('signal'),
                    'risk_decision':   a.get('risk_decision'),
                    'risk_score':      a.get('risk_score'),
                    'risk_flags':      a.get('risk_flags', []),
                    'shares':          (a.get('position_sizing') or {}).get('shares'),
                    'position_value':  (a.get('position_sizing') or {}).get('position_value_inr'),
                    'var_inr':         (a.get('var_metrics') or {}).get('position_var_inr'),
                    'cvar_inr':        (a.get('var_metrics') or {}).get('position_cvar_inr'),
                    'sector':          (a.get('market_risk') or {}).get('sector'),
                    'beta':            (a.get('market_risk') or {}).get('beta'),
                }
                for a in ranking
            ],
        }
        summary_path = self.risk_dir / 'risk_summary.json'
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        out_paths.append(str(summary_path))

        log_path = self.log_dir / f"risk_{datetime.now().strftime('%Y-%m-%d')}.txt"
        self._log(f"Done — {elapsed:.1f}s")
        with open(log_path, 'w') as f:
            f.write('\n'.join([
                'SwingTradeIQ RiskAgent',
                f"Date    : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"Duration: {elapsed:.1f}s",
                '',
                *self._log_lines,
            ]))

        print(f"\n{'='*65}")
        print(f"Done in {elapsed:.1f}s | ✅ {len(succeeded)} assessed | ❌ {len(failed)} errors")
        print(f"{'='*65}")

        approved = [a['ticker'] for a in assessments if a.get('risk_decision') in ('APPROVED', 'APPROVED_REDUCED')]
        return {
            'succeeded':    succeeded,
            'failed':       failed,
            'output_paths': out_paths,
            'elapsed_s':    round(elapsed, 1),
            'approved':     approved,
        }
