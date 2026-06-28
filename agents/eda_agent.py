# SwingTradeIQ — Agent 3/10: EDAAgent
# Built in Session 3.
# Handoff: reads data/validated/*_validated.csv → writes data/eda/

import time, json
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


class EDAAgent:
    """
    Agent 3/10 — SwingTradeIQ Exploratory Data Analysis Agent.

    Reads every *_validated.csv from data/validated/ and computes a
    structured EDA profile for each ticker plus a cross-ticker analysis.
    All outputs are JSON-serialisable dicts consumed by downstream agents.

    Per-ticker metrics
    ------------------
    - Price stats          : mean / std / min / max / quartiles for Close
    - Return stats         : daily log-returns, annualised return & vol,
                             skewness, kurtosis, % positive days
    - Sharpe ratio         : annualised, risk-free rate = 0 (adjusted later)
    - Drawdown             : max drawdown %, start / end / recovery dates
    - Volume stats         : mean, median, high-volume day count (> 2× avg)
    - Rolling snapshot     : 20-day and 50-day rolling mean & std of Close
                             as of the last date in the series

    Cross-ticker metrics
    --------------------
    - Daily log-return correlation matrix
    - Annualised return and volatility ranked table

    Outputs
    -------
        data/eda/{TICKER}_eda.json     — per-ticker profile
        data/eda/cross_ticker_eda.json — correlation matrix + ranking table
        data/eda/eda_summary.json      — run metadata + per-ticker headline stats
        logs/eda_{DATE}.txt            — run log
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir      = Path(base_dir)
        self.validated_dir = self.base_dir / 'data' / 'validated'
        self.eda_dir       = self.base_dir / 'data' / 'eda'
        self.log_dir       = self.base_dir / 'logs'

        for d in [self.eda_dir, self.log_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._log_lines: list[str] = []

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self._log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _load(self, csv_path: Path) -> pd.DataFrame:
        df = pd.read_csv(csv_path, index_col='Date', parse_dates=True)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.sort_index(inplace=True)
        return df

    def _r(self, val, decimals: int = 4):
        """Round a scalar for JSON output; pass through None."""
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return None
        return round(float(val), decimals)

    # ──────────────────────────────────────────────────────────────────────────
    # Per-ticker EDA
    # ──────────────────────────────────────────────────────────────────────────

    def _price_stats(self, df: pd.DataFrame) -> dict:
        c = df['Close']
        return {
            'mean':   self._r(c.mean(), 2),
            'std':    self._r(c.std(),  2),
            'min':    self._r(c.min(),  2),
            'max':    self._r(c.max(),  2),
            'q25':    self._r(c.quantile(0.25), 2),
            'median': self._r(c.median(), 2),
            'q75':    self._r(c.quantile(0.75), 2),
        }

    def _return_stats(self, log_ret: pd.Series) -> dict:
        n   = len(log_ret)
        ann = TRADING_DAYS_PER_YEAR
        mean_daily   = log_ret.mean()
        std_daily    = log_ret.std()
        ann_return   = mean_daily * ann
        ann_vol      = std_daily * np.sqrt(ann)
        sharpe       = ann_return / ann_vol if ann_vol else None
        positive_pct = (log_ret > 0).sum() / n if n else None
        return {
            'mean_daily_log_return':  self._r(mean_daily, 6),
            'std_daily_log_return':   self._r(std_daily,  6),
            'annualised_return_pct':  self._r(ann_return * 100, 2),
            'annualised_volatility_pct': self._r(ann_vol * 100, 2),
            'sharpe_ratio':           self._r(sharpe, 3),
            'skewness':               self._r(log_ret.skew(), 4),
            'kurtosis':               self._r(log_ret.kurt(), 4),
            'positive_days_pct':      self._r(positive_pct * 100, 1) if positive_pct is not None else None,
            'observations':           n,
        }

    def _drawdown(self, df: pd.DataFrame) -> dict:
        close      = df['Close']
        roll_max   = close.cummax()
        dd_series  = (close - roll_max) / roll_max

        max_dd     = dd_series.min()
        end_idx    = dd_series.idxmin()
        peak_idx   = close[:end_idx].idxmax()

        # Recovery: first date after end_idx where price >= peak price
        peak_price = close[peak_idx]
        recovery_candidates = close[end_idx:][close[end_idx:] >= peak_price]
        recovery_idx = recovery_candidates.index[0] if len(recovery_candidates) else None

        return {
            'max_drawdown_pct':   self._r(max_dd * 100, 2),
            'peak_date':          str(peak_idx.date()),
            'trough_date':        str(end_idx.date()),
            'recovery_date':      str(recovery_idx.date()) if recovery_idx else None,
            'drawdown_days':      int((end_idx - peak_idx).days),
        }

    def _volume_stats(self, df: pd.DataFrame) -> dict:
        v    = df['Volume']
        avg  = v.mean()
        high = int((v > 2 * avg).sum())
        return {
            'mean_volume':            int(avg),
            'median_volume':          int(v.median()),
            'max_volume':             int(v.max()),
            'high_volume_days':       high,
            'high_volume_threshold':  int(2 * avg),
        }

    def _rolling_snapshot(self, df: pd.DataFrame) -> dict:
        close = df['Close']
        snap  = {}
        for w in (20, 50):
            rm  = close.rolling(w).mean()
            rs  = close.rolling(w).std()
            snap[f'{w}d_mean'] = self._r(rm.iloc[-1], 2)
            snap[f'{w}d_std']  = self._r(rs.iloc[-1], 2)
            # price vs rolling mean: positive = above MA
            snap[f'{w}d_pct_above_ma'] = self._r(
                (close.iloc[-1] - rm.iloc[-1]) / rm.iloc[-1] * 100, 2
            )
        return snap

    def _analyse_one(self, csv_path: Path) -> dict:
        ticker = csv_path.stem.replace('_validated', '')
        self._log(f"EDA: {ticker}")

        df      = self._load(csv_path)
        log_ret = np.log(df['Close'] / df['Close'].shift(1)).dropna()

        profile = {
            'ticker':     ticker,
            'date_range': {
                'start': str(df.index[0].date()),
                'end':   str(df.index[-1].date()),
                'rows':  len(df),
            },
            'price_stats':     self._price_stats(df),
            'return_stats':    self._return_stats(log_ret),
            'drawdown':        self._drawdown(df),
            'volume_stats':    self._volume_stats(df),
            'rolling_snapshot': self._rolling_snapshot(df),
        }

        out_path = self.eda_dir / f"{ticker}_eda.json"
        with open(out_path, 'w') as f:
            json.dump(profile, f, indent=2, default=str)

        return profile

    # ──────────────────────────────────────────────────────────────────────────
    # Cross-ticker analysis
    # ──────────────────────────────────────────────────────────────────────────

    def _cross_ticker(self, profiles: list[dict], csv_paths: list[Path]) -> dict:
        returns = {}
        for path in csv_paths:
            ticker = path.stem.replace('_validated', '')
            df     = self._load(path)
            returns[ticker] = np.log(df['Close'] / df['Close'].shift(1)).dropna()

        ret_df  = pd.DataFrame(returns).dropna()
        corr    = ret_df.corr().round(4)

        ranking = sorted(
            [
                {
                    'ticker':                  p['ticker'],
                    'annualised_return_pct':   p['return_stats']['annualised_return_pct'],
                    'annualised_volatility_pct': p['return_stats']['annualised_volatility_pct'],
                    'sharpe_ratio':            p['return_stats']['sharpe_ratio'],
                    'max_drawdown_pct':        p['drawdown']['max_drawdown_pct'],
                }
                for p in profiles
            ],
            key=lambda x: (x['sharpe_ratio'] or 0),
            reverse=True,
        )

        cross = {
            'return_correlation': corr.to_dict(),
            'ranking_by_sharpe':  ranking,
        }

        out_path = self.eda_dir / 'cross_ticker_eda.json'
        with open(out_path, 'w') as f:
            json.dump(cross, f, indent=2, default=str)

        return cross

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        start_time  = time.time()
        self._log_lines = []

        csv_paths = sorted(self.validated_dir.glob('*_validated.csv'))
        if not csv_paths:
            print("EDAAgent: no *_validated.csv files found in data/validated/")
            return {'succeeded': [], 'failed': [], 'output_paths': [], 'elapsed_s': 0.0}

        self._log(f"EDAAgent — {len(csv_paths)} tickers")
        print(f"{'='*55}")
        print(f"EDAAgent.run() — {len(csv_paths)} validated files")
        print(f"{'='*55}")

        profiles   = []
        succeeded  = []
        failed     = []
        out_paths  = []

        for i, path in enumerate(csv_paths, 1):
            ticker = path.stem.replace('_validated', '')
            print(f"[{i:>3}/{len(csv_paths)}] {ticker:<18}", end='', flush=True)
            try:
                profile = self._analyse_one(path)
                profiles.append(profile)
                succeeded.append(ticker)
                out_paths.append(str(self.eda_dir / f"{ticker}_eda.json"))
                rs = profile['return_stats']
                dd = profile['drawdown']
                print(
                    f"  ✅ ret={rs['annualised_return_pct']:+.1f}%"
                    f"  vol={rs['annualised_volatility_pct']:.1f}%"
                    f"  sharpe={rs['sharpe_ratio']:.2f}"
                    f"  maxDD={dd['max_drawdown_pct']:.1f}%"
                )
            except Exception as exc:
                self._log(f"  ERROR {ticker}: {exc}")
                failed.append({'ticker': ticker, 'reason': str(exc)})
                print(f"  ❌ {exc}")

        # Cross-ticker analysis (only if >= 2 tickers succeeded)
        cross_path = None
        if len(profiles) >= 2:
            print(f"\nComputing cross-ticker analysis ({len(profiles)} tickers)...")
            cross     = self._cross_ticker(profiles, csv_paths)
            cross_path = str(self.eda_dir / 'cross_ticker_eda.json')
            out_paths.append(cross_path)

            print("\nReturn correlation matrix:")
            corr_df = pd.DataFrame(cross['return_correlation'])
            print(corr_df.to_string())

            print("\nRanked by Sharpe ratio:")
            for rank, row in enumerate(cross['ranking_by_sharpe'], 1):
                print(
                    f"  {rank}. {row['ticker']:<14}"
                    f"  ret={row['annualised_return_pct']:+.1f}%"
                    f"  vol={row['annualised_volatility_pct']:.1f}%"
                    f"  sharpe={row['sharpe_ratio']:.2f}"
                    f"  maxDD={row['max_drawdown_pct']:.1f}%"
                )

        # EDA summary
        elapsed  = time.time() - start_time
        summary  = {
            'run_at':       datetime.now().isoformat(),
            'tickers':      succeeded,
            'failed':       failed,
            'elapsed_s':    round(elapsed, 1),
            'per_ticker':   [
                {
                    'ticker':                  p['ticker'],
                    'rows':                    p['date_range']['rows'],
                    'annualised_return_pct':   p['return_stats']['annualised_return_pct'],
                    'annualised_volatility_pct': p['return_stats']['annualised_volatility_pct'],
                    'sharpe_ratio':            p['return_stats']['sharpe_ratio'],
                    'max_drawdown_pct':        p['drawdown']['max_drawdown_pct'],
                }
                for p in profiles
            ],
        }
        summary_path = self.eda_dir / 'eda_summary.json'
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        out_paths.append(str(summary_path))

        # Write log
        log_path = self.log_dir / f"eda_{datetime.now().strftime('%Y-%m-%d')}.txt"
        self._log(f"Done — {len(succeeded)} succeeded, {len(failed)} failed, {elapsed:.1f}s")
        with open(log_path, 'w') as f:
            f.write('\n'.join([
                'SwingTradeIQ EDAAgent',
                f"Date    : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"Duration: {elapsed:.1f}s",
                '',
                *self._log_lines,
            ]))

        print(f"\n{'='*55}")
        print(f"Done in {elapsed:.1f}s | ✅ {len(succeeded)} | ❌ {len(failed)}")
        print(f"Outputs: {self.eda_dir}")
        print(f"{'='*55}")

        return {
            'succeeded':    succeeded,
            'failed':       failed,
            'output_paths': out_paths,
            'elapsed_s':    round(elapsed, 1),
        }
