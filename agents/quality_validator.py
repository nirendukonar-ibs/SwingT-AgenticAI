# SwingTradeIQ — Agent 2/10: QualityValidatorAgent
# Built in Session 2.
# Handoff: reads data/raw/*_ohlcv.csv → writes data/validated/*_validated.csv
#           + data/validated/validation_report.json

import time, json, warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# Mirrors the constant in CLAUDE.md / DataCollectorAgent
MIN_HISTORY_ROWS = 100

# Thresholds
MAX_MISSING_CLOSE_FRAC  = 0.05   # >5 % NaN closes → reject ticker
MAX_GAP_CALENDAR_DAYS   = 10     # gap between consecutive rows > this is flagged
SPIKE_SIGMA             = 5.0    # |log-return| > 5σ flagged as spike
MAX_STALE_TRADING_DAYS  = 5      # last date must be within 5 trading days of today


class QualityValidatorAgent:
    """
    Agent 2/10 — SwingTradeIQ Quality Validation Agent.

    Reads every *_ohlcv.csv from data/raw/, runs a suite of quality checks,
    repairs minor issues in-place, and writes cleaned files to data/validated/.
    Tickers that fail hard constraints are excluded from downstream agents.

    Checks performed
    ----------------
    1. Minimum row count  (>= MIN_HISTORY_ROWS after repair)
    2. Missing values     (NaN in OHLCV columns)
    3. Zero / negative prices
    4. OHLC consistency   (High >= max(O,C), Low <= min(O,C))
    5. Duplicate dates
    6. Calendar gaps      (consecutive rows > MAX_GAP_CALENDAR_DAYS apart)
    7. Price spikes       (|log-return| > SPIKE_SIGMA σ)
    8. Staleness          (last row older than MAX_STALE_TRADING_DAYS trading days)

    Repair actions
    --------------
    - Remove exact duplicate dates (keep first occurrence)
    - Forward-fill NaN in Open / High / Low / Volume where Close exists
    - Drop rows with NaN Close (unrecoverable)
    - Drop OHLC-inconsistent rows (corrupt tick)

    Usage
    -----
        agent = QualityValidatorAgent('/path/to/SwingTradeIQ')
        result = agent.run()

    Outputs
    -------
        data/validated/{TICKER}_validated.csv  — one per accepted ticker
        data/validated/validation_report.json  — full audit trail
        logs/validate_{DATE}.txt               — run log
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir       = Path(base_dir)
        self.raw_dir        = self.base_dir / 'data' / 'raw'
        self.validated_dir  = self.base_dir / 'data' / 'validated'
        self.log_dir        = self.base_dir / 'logs'

        for d in [self.validated_dir, self.log_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._log_lines: list[str] = []

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        stamped = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        self._log_lines.append(stamped)

    def _load_raw(self, csv_path: Path) -> pd.DataFrame | None:
        try:
            df = pd.read_csv(csv_path, index_col='Date', parse_dates=True)
            df.index = pd.to_datetime(df.index, utc=False)
            df.index = df.index.tz_localize(None)
            df.sort_index(inplace=True)
            return df
        except Exception as exc:
            self._log(f"  LOAD ERROR: {exc}")
            return None

    def _check_missing(self, df: pd.DataFrame) -> dict:
        counts = df[['Open', 'High', 'Low', 'Close', 'Volume']].isna().sum().to_dict()
        return {col: int(v) for col, v in counts.items()}

    def _check_negative_prices(self, df: pd.DataFrame) -> int:
        mask = (df[['Open', 'High', 'Low', 'Close']] <= 0).any(axis=1)
        return int(mask.sum())

    def _check_ohlc_consistency(self, df: pd.DataFrame) -> int:
        bad = (
            (df['High'] < df[['Open', 'Close']].max(axis=1)) |
            (df['Low']  > df[['Open', 'Close']].min(axis=1))
        )
        return int(bad.sum())

    def _check_duplicates(self, df: pd.DataFrame) -> int:
        return int(df.index.duplicated().sum())

    def _check_gaps(self, df: pd.DataFrame) -> list[dict]:
        gaps = []
        dates = df.index.to_series().reset_index(drop=True)
        deltas = dates.diff().dt.days.dropna()
        large = deltas[deltas > MAX_GAP_CALENDAR_DAYS]
        for idx, days in large.items():
            gaps.append({
                'after_date': str(dates.iloc[idx - 1].date()),
                'before_date': str(dates.iloc[idx].date()),
                'gap_days': int(days),
            })
        return gaps

    def _check_spikes(self, df: pd.DataFrame) -> list[str]:
        log_ret = np.log(df['Close'] / df['Close'].shift(1)).dropna()
        sigma   = log_ret.std()
        if sigma == 0:
            return []
        spikes = log_ret[log_ret.abs() > SPIKE_SIGMA * sigma]
        return [str(d.date()) for d in spikes.index]

    def _check_staleness(self, df: pd.DataFrame) -> dict:
        last_date  = df.index[-1].date()
        today      = datetime.today().date()
        cal_days   = (today - last_date).days
        # rough trading-days estimate (5/7 of calendar days)
        trading_days_est = int(cal_days * 5 / 7)
        stale = trading_days_est > MAX_STALE_TRADING_DAYS
        return {
            'last_date': str(last_date),
            'calendar_days_old': cal_days,
            'est_trading_days_old': trading_days_est,
            'is_stale': stale,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Repair
    # ──────────────────────────────────────────────────────────────────────────

    def _repair(self, df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        repairs = {}

        # 1. Remove duplicate dates
        dupes = int(df.index.duplicated().sum())
        if dupes:
            df = df[~df.index.duplicated(keep='first')]
            repairs['duplicates_removed'] = dupes

        # 2. Drop rows with NaN Close (unrecoverable)
        nan_close = int(df['Close'].isna().sum())
        if nan_close:
            df = df.dropna(subset=['Close'])
            repairs['nan_close_rows_dropped'] = nan_close

        # 3. Forward-fill NaN in O/H/L/V where Close exists
        for col in ['Open', 'High', 'Low', 'Volume']:
            n = int(df[col].isna().sum())
            if n:
                df[col] = df[col].ffill()
                repairs[f'ffill_{col.lower()}'] = n

        # 4. Drop OHLC-inconsistent rows
        bad_mask = (
            (df['High'] < df[['Open', 'Close']].max(axis=1)) |
            (df['Low']  > df[['Open', 'Close']].min(axis=1))
        )
        n_bad = int(bad_mask.sum())
        if n_bad:
            df = df[~bad_mask]
            repairs['ohlc_inconsistent_rows_dropped'] = n_bad

        # 5. Drop rows with zero/negative prices
        neg_mask = (df[['Open', 'High', 'Low', 'Close']] <= 0).any(axis=1)
        n_neg = int(neg_mask.sum())
        if n_neg:
            df = df[~neg_mask]
            repairs['negative_price_rows_dropped'] = n_neg

        return df, repairs

    # ──────────────────────────────────────────────────────────────────────────
    # Per-ticker validation
    # ──────────────────────────────────────────────────────────────────────────

    def _validate_one(self, csv_path: Path) -> dict:
        ticker = csv_path.stem.replace('_ohlcv', '')
        self._log(f"Validating {ticker}")

        report: dict = {
            'ticker': ticker,
            'source_file': str(csv_path),
            'status': 'unknown',
            'checks': {},
            'repairs': {},
            'output_path': None,
            'rows_raw': 0,
            'rows_validated': 0,
        }

        df = self._load_raw(csv_path)
        if df is None:
            report['status'] = 'failed'
            report['failure_reason'] = 'could not load CSV'
            return report

        report['rows_raw'] = len(df)

        # ── Pre-repair checks (for audit) ────────────────────────────────────
        report['checks']['missing_values']       = self._check_missing(df)
        report['checks']['negative_price_rows']  = self._check_negative_prices(df)
        report['checks']['ohlc_inconsistent_rows'] = self._check_ohlc_consistency(df)
        report['checks']['duplicate_dates']      = self._check_duplicates(df)
        report['checks']['calendar_gaps']        = self._check_gaps(df)
        report['checks']['price_spikes']         = self._check_spikes(df)
        report['checks']['staleness']            = self._check_staleness(df)

        # ── Repair ───────────────────────────────────────────────────────────
        df, repairs = self._repair(df)
        report['repairs'] = repairs

        # ── Hard rejection rules (post-repair) ───────────────────────────────
        close_nan_frac = df['Close'].isna().sum() / max(len(df), 1)
        if close_nan_frac > MAX_MISSING_CLOSE_FRAC:
            report['status'] = 'failed'
            report['failure_reason'] = f'too many NaN closes after repair ({close_nan_frac:.1%})'
            return report

        if len(df) < MIN_HISTORY_ROWS:
            report['status'] = 'failed'
            report['failure_reason'] = f'only {len(df)} rows after repair (need {MIN_HISTORY_ROWS})'
            return report

        if report['checks']['staleness']['is_stale']:
            report['status'] = 'warning_stale'  # pass but flagged
        else:
            report['status'] = 'passed'

        # ── Write validated CSV ───────────────────────────────────────────────
        out_path = self.validated_dir / f"{ticker}_validated.csv"
        df.to_csv(out_path)

        report['rows_validated'] = len(df)
        report['output_path']    = str(out_path)
        return report

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        start_time = time.time()
        self._log_lines = []

        raw_files = sorted(self.raw_dir.glob('*_ohlcv.csv'))
        if not raw_files:
            print("QualityValidatorAgent: no *_ohlcv.csv files found in data/raw/")
            return {
                'succeeded': [], 'failed': [],
                'output_paths': [], 'elapsed_s': 0.0,
                'report_path': None,
            }

        self._log(f"QualityValidatorAgent — {len(raw_files)} files to validate")
        print(f"{'='*55}")
        print(f"QualityValidatorAgent.run() — {len(raw_files)} raw files")
        print(f"{'='*55}")

        reports   = []
        succeeded = []
        failed    = []
        out_paths = []

        for i, csv_path in enumerate(raw_files, 1):
            ticker = csv_path.stem.replace('_ohlcv', '')
            print(f"[{i:>3}/{len(raw_files)}] {ticker:<18}", end='', flush=True)

            report = self._validate_one(csv_path)
            reports.append(report)

            if report['status'] in ('passed', 'warning_stale'):
                flag = '⚠' if report['status'] == 'warning_stale' else '✅'
                print(
                    f"  {flag} {report['rows_raw']}→{report['rows_validated']} rows"
                    f"  repairs={len(report['repairs'])}"
                    f"  spikes={len(report['checks'].get('price_spikes', []))}"
                )
                succeeded.append(ticker)
                out_paths.append(report['output_path'])
            else:
                print(f"  ❌ {report.get('failure_reason','')}")
                failed.append({'ticker': ticker, 'reason': report.get('failure_reason', '')})

        # ── Write consolidated report ─────────────────────────────────────────
        report_data = {
            'run_at': datetime.now().isoformat(),
            'total': len(raw_files),
            'passed': len(succeeded),
            'failed': len(failed),
            'thresholds': {
                'min_history_rows': MIN_HISTORY_ROWS,
                'max_missing_close_frac': MAX_MISSING_CLOSE_FRAC,
                'max_gap_calendar_days': MAX_GAP_CALENDAR_DAYS,
                'spike_sigma': SPIKE_SIGMA,
                'max_stale_trading_days': MAX_STALE_TRADING_DAYS,
            },
            'tickers': reports,
        }
        report_path = self.validated_dir / 'validation_report.json'
        with open(report_path, 'w') as f:
            json.dump(report_data, f, indent=2, default=str)

        # ── Write log ────────────────────────────────────────────────────────
        elapsed  = time.time() - start_time
        log_path = self.log_dir / f"validate_{datetime.now().strftime('%Y-%m-%d')}.txt"
        self._log(f"Done — {len(succeeded)} passed, {len(failed)} failed, {elapsed:.1f}s")
        with open(log_path, 'w') as f:
            f.write('\n'.join([
                'SwingTradeIQ QualityValidatorAgent',
                f"Date    : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"Duration: {elapsed:.1f}s",
                f"Passed  : {len(succeeded)}",
                f"Failed  : {len(failed)}",
                '',
                *self._log_lines,
            ]))

        print(f"{'='*55}")
        print(f"Done in {elapsed:.1f}s | ✅ {len(succeeded)} passed | ❌ {len(failed)} failed")
        print(f"Report : {report_path}")
        print(f"{'='*55}")

        return {
            'succeeded':    succeeded,
            'failed':       failed,
            'output_paths': out_paths,
            'elapsed_s':    round(elapsed, 1),
            'report_path':  str(report_path),
        }
