# SwingTradeIQ — Agent 5/10: TechnicalAgent
# Built in Session 5.
# Handoff: reads data/validated/*_validated.csv → writes data/technical/

import time, json
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

import ta.trend     as ta_trend
import ta.momentum  as ta_mom
import ta.volatility as ta_vol
import ta.volume    as ta_vol2

TRADING_DAYS_PER_YEAR = 252

# ── Scoring weights (must sum to 1.0) ────────────────────────────────────────
WEIGHTS = {
    'ma_trend':    0.25,
    'rsi':         0.20,
    'macd':        0.20,
    'bollinger':   0.15,
    'adx':         0.10,
    'volume_obv':  0.10,
}


class TechnicalAgent:
    """
    Agent 5/10 — SwingTradeIQ Technical Analysis Agent.

    Computes a full suite of technical indicators on validated OHLCV data,
    derives per-indicator signals, and rolls them into a composite technical
    score (0–10) used by downstream agents for trade filtering.

    Indicators computed
    -------------------
    Trend     : SMA 20 / 50 / 200, EMA 12 / 26
    Momentum  : MACD (12/26/9), RSI 14, Stochastic %K/%D (14/3/3)
    Volatility: Bollinger Bands (20, 2σ), ATR 14
    Volume    : OBV, 20-day volume MA

    Scoring components (weighted average → 0–10)
    ---------------------------------------------
    MA Trend   (25%) — price alignment vs SMA20/50/200 + golden/death cross
    RSI        (20%) — optimal zone 50-65 scores highest; extremes penalised
    MACD       (20%) — signal/histogram crossover direction
    Bollinger  (15%) — price position within the band envelope
    ADX        (10%) — trend strength and direction via ±DI
    OBV Trend  (10%) — 20-day OBV slope direction

    Signal labels: STRONG_BUY · BUY · NEUTRAL · SELL · STRONG_SELL

    Outputs
    -------
        data/technical/{TICKER}_technical.json  — full indicator set + score
        data/technical/technical_summary.json   — ranked summary across tickers
        logs/technical_{DATE}.txt               — run log
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir      = Path(base_dir)
        self.validated_dir = self.base_dir / 'data' / 'validated'
        self.technical_dir = self.base_dir / 'data' / 'technical'
        self.log_dir       = self.base_dir / 'logs'

        for d in [self.technical_dir, self.log_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._log_lines: list[str] = []

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self._log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _load(self, path: Path) -> pd.DataFrame:
        df = pd.read_csv(path, index_col='Date', parse_dates=True)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.sort_index(inplace=True)
        return df

    def _r(self, v, d: int = 4):
        if v is None: return None
        try:
            f = float(v)
            return None if np.isnan(f) or np.isinf(f) else round(f, d)
        except (TypeError, ValueError):
            return None

    def _label(self, score: float) -> str:
        if score >= 7.5: return 'STRONG_BUY'
        if score >= 6.0: return 'BUY'
        if score >= 4.0: return 'NEUTRAL'
        if score >= 2.5: return 'SELL'
        return 'STRONG_SELL'

    # ──────────────────────────────────────────────────────────────────────────
    # Indicator computation
    # ──────────────────────────────────────────────────────────────────────────

    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        c, h, l, v = df['Close'], df['High'], df['Low'], df['Volume']

        # Moving averages
        df['sma20']  = ta_trend.SMAIndicator(c, 20).sma_indicator()
        df['sma50']  = ta_trend.SMAIndicator(c, 50).sma_indicator()
        df['sma200'] = ta_trend.SMAIndicator(c, 200).sma_indicator()
        df['ema12']  = ta_trend.EMAIndicator(c, 12).ema_indicator()
        df['ema26']  = ta_trend.EMAIndicator(c, 26).ema_indicator()

        # MACD
        macd_obj       = ta_trend.MACD(c, 26, 12, 9)
        df['macd']     = macd_obj.macd()
        df['macd_sig'] = macd_obj.macd_signal()
        df['macd_hist']= macd_obj.macd_diff()

        # RSI
        df['rsi'] = ta_mom.RSIIndicator(c, 14).rsi()

        # Stochastic
        stoch         = ta_mom.StochasticOscillator(h, l, c, 14, 3)
        df['stoch_k'] = stoch.stoch()
        df['stoch_d'] = stoch.stoch_signal()

        # Bollinger Bands
        bb            = ta_vol.BollingerBands(c, 20, 2)
        df['bb_upper']= bb.bollinger_hband()
        df['bb_mid']  = bb.bollinger_mavg()
        df['bb_lower']= bb.bollinger_lband()
        df['bb_pct']  = bb.bollinger_pband()   # 0=at lower, 1=at upper

        # ATR
        df['atr14'] = ta_vol.AverageTrueRange(h, l, c, 14).average_true_range()

        # ADX
        adx_obj    = ta_trend.ADXIndicator(h, l, c, 14)
        df['adx']  = adx_obj.adx()
        df['adx_pos'] = adx_obj.adx_pos()   # +DI
        df['adx_neg'] = adx_obj.adx_neg()   # -DI

        # OBV
        df['obv'] = ta_vol2.OnBalanceVolumeIndicator(c, v).on_balance_volume()

        return df

    # ──────────────────────────────────────────────────────────────────────────
    # Scoring (each component returns 0–10)
    # ──────────────────────────────────────────────────────────────────────────

    def _score_ma_trend(self, row: pd.Series) -> tuple[float, dict]:
        """Price alignment across SMA20/50/200. Max 4 conditions → mapped 0-10."""
        price = row['Close']
        points = 0
        details = {}

        for col, label in [('sma20','vs_sma20'), ('sma50','vs_sma50'), ('sma200','vs_sma200')]:
            if pd.notna(row[col]):
                above = price > row[col]
                details[label] = 'above' if above else 'below'
                if above: points += 1

        # Golden/death cross: SMA50 vs SMA200
        if pd.notna(row['sma50']) and pd.notna(row['sma200']):
            cross = row['sma50'] > row['sma200']
            details['sma50_vs_sma200'] = 'golden' if cross else 'death'
            if cross: points += 1

        max_pts = 4
        score   = round(points / max_pts * 10, 2)
        details['score'] = score
        return score, details

    def _score_rsi(self, rsi: float) -> tuple[float, dict]:
        """Optimal zone 50-65. Overbought >75 and oversold <25 both penalised."""
        if rsi >= 75:   score = 3.0;  zone = 'overbought'
        elif rsi >= 65: score = 7.0;  zone = 'bullish'
        elif rsi >= 50: score = 9.0;  zone = 'optimal'
        elif rsi >= 40: score = 5.0;  zone = 'neutral'
        elif rsi >= 30: score = 3.0;  zone = 'bearish'
        else:           score = 5.0;  zone = 'oversold'  # potential reversal
        return score, {'rsi': self._r(rsi, 1), 'zone': zone, 'score': score}

    def _score_macd(self, row: pd.Series) -> tuple[float, dict]:
        macd, sig, hist = row['macd'], row['macd_sig'], row['macd_hist']
        if pd.isna(macd) or pd.isna(sig):
            return 5.0, {'score': 5.0, 'note': 'insufficient data'}

        bullish_cross = macd > sig
        positive_macd = macd > 0
        hist_rising   = hist > 0

        if bullish_cross and positive_macd:   score = 9.0
        elif bullish_cross and not positive_macd: score = 6.5
        elif not bullish_cross and positive_macd: score = 4.5
        else:                                      score = 2.0

        return score, {
            'macd':        self._r(macd, 4),
            'signal':      self._r(sig,  4),
            'histogram':   self._r(hist, 4),
            'cross':       'bullish' if bullish_cross else 'bearish',
            'macd_above_zero': positive_macd,
            'score':       score,
        }

    def _score_bollinger(self, row: pd.Series) -> tuple[float, dict]:
        pct = row.get('bb_pct')          # 0 = at lower band, 1 = at upper band
        if pd.isna(pct):
            return 5.0, {'score': 5.0, 'note': 'insufficient data'}

        if pct > 1.0:    score = 3.0;  zone = 'above_upper'   # over-extended
        elif pct > 0.8:  score = 7.0;  zone = 'upper_zone'
        elif pct > 0.5:  score = 9.0;  zone = 'above_mid'     # bullish sweet spot
        elif pct > 0.2:  score = 5.0;  zone = 'below_mid'
        elif pct >= 0.0: score = 4.0;  zone = 'lower_zone'
        else:            score = 6.0;  zone = 'below_lower'   # oversold bounce

        return score, {
            'bb_pct':   self._r(pct, 3),
            'bb_upper': self._r(row['bb_upper'], 2),
            'bb_mid':   self._r(row['bb_mid'],   2),
            'bb_lower': self._r(row['bb_lower'], 2),
            'zone':     zone,
            'score':    score,
        }

    def _score_adx(self, row: pd.Series) -> tuple[float, dict]:
        adx, pos, neg = row['adx'], row['adx_pos'], row['adx_neg']
        if pd.isna(adx):
            return 5.0, {'score': 5.0, 'note': 'insufficient data'}

        trending_up = pos > neg
        if adx > 40:
            score = 9.0 if trending_up else 1.0
            strength = 'very_strong'
        elif adx > 25:
            score = 7.5 if trending_up else 2.5
            strength = 'strong'
        elif adx > 20:
            score = 6.0 if trending_up else 4.0
            strength = 'moderate'
        else:
            score = 5.0   # weak / no trend — direction not reliable
            strength = 'weak'

        return score, {
            'adx':      self._r(adx, 1),
            'plus_di':  self._r(pos, 1),
            'minus_di': self._r(neg, 1),
            'strength': strength,
            'direction':'up' if trending_up else 'down',
            'score':    score,
        }

    def _score_obv(self, df: pd.DataFrame) -> tuple[float, dict]:
        """20-day OBV slope: positive → accumulation, negative → distribution."""
        obv = df['obv'].dropna()
        if len(obv) < 20:
            return 5.0, {'score': 5.0, 'note': 'insufficient data'}

        recent = obv.iloc[-20:]
        # Normalised slope: change per day as fraction of start value
        slope = (recent.iloc[-1] - recent.iloc[0]) / (abs(recent.iloc[0]) + 1)

        if slope > 0.10:   score = 9.0;  trend = 'strong_accumulation'
        elif slope > 0.02: score = 7.0;  trend = 'accumulation'
        elif slope > -0.02:score = 5.0;  trend = 'neutral'
        elif slope > -0.10:score = 3.0;  trend = 'distribution'
        else:              score = 1.0;  trend = 'strong_distribution'

        return score, {
            'obv_current':  int(obv.iloc[-1]),
            'obv_20d_ago':  int(recent.iloc[0]),
            'slope_20d':    self._r(slope, 4),
            'trend':        trend,
            'score':        score,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Per-ticker analysis
    # ──────────────────────────────────────────────────────────────────────────

    def _analyse_one(self, csv_path: Path) -> dict:
        ticker = csv_path.stem.replace('_validated', '')
        df     = self._load(csv_path)
        df     = self._compute_indicators(df)
        last   = df.iloc[-1]
        price  = float(last['Close'])

        # ── Component scores ─────────────────────────────────────────────────
        s_ma,   d_ma   = self._score_ma_trend(last)
        s_rsi,  d_rsi  = self._score_rsi(float(last['rsi']))   if pd.notna(last['rsi']) else (5.0, {})
        s_macd, d_macd = self._score_macd(last)
        s_bb,   d_bb   = self._score_bollinger(last)
        s_adx,  d_adx  = self._score_adx(last)
        s_obv,  d_obv  = self._score_obv(df)

        components = {
            'ma_trend':   {'score': s_ma,   'weight': WEIGHTS['ma_trend'],   'detail': d_ma},
            'rsi':        {'score': s_rsi,  'weight': WEIGHTS['rsi'],        'detail': d_rsi},
            'macd':       {'score': s_macd, 'weight': WEIGHTS['macd'],       'detail': d_macd},
            'bollinger':  {'score': s_bb,   'weight': WEIGHTS['bollinger'],  'detail': d_bb},
            'adx':        {'score': s_adx,  'weight': WEIGHTS['adx'],        'detail': d_adx},
            'volume_obv': {'score': s_obv,  'weight': WEIGHTS['volume_obv'], 'detail': d_obv},
        }

        composite = round(
            sum(v['score'] * v['weight'] for v in components.values()), 2
        )
        signal = self._label(composite)

        # ── Snapshot of last-row indicator values ─────────────────────────────
        atr_pct = self._r(last['atr14'] / price * 100, 2) if price else None
        snapshot = {
            'price':      self._r(price, 2),
            'sma20':      self._r(last['sma20'],  2),
            'sma50':      self._r(last['sma50'],  2),
            'sma200':     self._r(last['sma200'], 2),
            'ema12':      self._r(last['ema12'],  2),
            'ema26':      self._r(last['ema26'],  2),
            'rsi14':      self._r(last['rsi'],    1),
            'stoch_k':    self._r(last['stoch_k'],1),
            'stoch_d':    self._r(last['stoch_d'],1),
            'atr14':      self._r(last['atr14'],  2),
            'atr_pct':    atr_pct,
        }

        # ── 52-week stats ────────────────────────────────────────────────────
        yr_high = float(df['High'].max())
        yr_low  = float(df['Low'].min())
        pct_from_high = round((price - yr_high) / yr_high * 100, 1)
        pct_from_low  = round((price - yr_low)  / yr_low  * 100, 1)

        profile = {
            'ticker':          ticker,
            'as_of_date':      str(df.index[-1].date()),
            'technical_score': composite,
            'signal':          signal,
            'snapshot':        snapshot,
            '52w': {
                'high':           self._r(yr_high, 2),
                'low':            self._r(yr_low,  2),
                'pct_from_high':  pct_from_high,
                'pct_from_low':   pct_from_low,
            },
            'components':  components,
        }

        out_path = self.technical_dir / f"{ticker}_technical.json"
        with open(out_path, 'w') as f:
            json.dump(profile, f, indent=2, default=str)

        return profile

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        start_time      = time.time()
        self._log_lines = []

        csv_paths = sorted(self.validated_dir.glob('*_validated.csv'))
        if not csv_paths:
            print("TechnicalAgent: no *_validated.csv files in data/validated/")
            return {'succeeded': [], 'failed': [], 'output_paths': [], 'elapsed_s': 0.0}

        self._log(f"TechnicalAgent — {len(csv_paths)} tickers")
        print(f"{'='*65}")
        print(f"TechnicalAgent.run() — {len(csv_paths)} validated files")
        print(f"{'='*65}")

        profiles  = []
        succeeded = []
        failed    = []
        out_paths = []

        for i, path in enumerate(csv_paths, 1):
            ticker = path.stem.replace('_validated', '')
            print(f"[{i:>3}/{len(csv_paths)}] {ticker:<16}", end='', flush=True)
            try:
                profile = self._analyse_one(path)
                profiles.append(profile)
                succeeded.append(ticker)
                out_paths.append(str(self.technical_dir / f"{ticker}_technical.json"))

                sn = profile['snapshot']
                print(
                    f"  {profile['signal']:<12}"
                    f"  score={profile['technical_score']:.1f}/10"
                    f"  RSI={sn['rsi14']}"
                    f"  ATR%={sn['atr_pct']}%"
                    f"  52w-hi={profile['52w']['pct_from_high']}%"
                )
                self._log(f"{ticker}: score={profile['technical_score']} signal={profile['signal']}")
            except Exception as exc:
                import traceback
                failed.append({'ticker': ticker, 'reason': str(exc)})
                print(f"  ❌ {exc}")
                self._log(f"ERROR {ticker}: {exc}\n{traceback.format_exc()}")

        # ── Summary table ─────────────────────────────────────────────────────
        ranking = sorted(profiles, key=lambda p: p['technical_score'], reverse=True)

        print(f"\n{'─'*65}")
        print(f"{'Rank':<5} {'Ticker':<14} {'Signal':<13} {'Score':>5}  "
              f"{'RSI':>5}  {'vs SMA200':>10}  {'52w-hi%':>8}")
        print(f"{'─'*65}")
        for i, p in enumerate(ranking, 1):
            sn   = p['snapshot']
            ma_d = p['components']['ma_trend']['detail']
            vs200 = ma_d.get('vs_sma200', 'n/a')
            print(
                f"{i:<5} {p['ticker']:<14} {p['signal']:<13} "
                f"{p['technical_score']:>5.1f}  "
                f"{str(sn['rsi14']):>5}  "
                f"{vs200:>10}  "
                f"{p['52w']['pct_from_high']:>8.1f}%"
            )

        summary = {
            'run_at':   datetime.now().isoformat(),
            'total':    len(csv_paths),
            'ranking': [
                {
                    'rank':            i + 1,
                    'ticker':          p['ticker'],
                    'technical_score': p['technical_score'],
                    'signal':          p['signal'],
                    'rsi14':           p['snapshot']['rsi14'],
                    'atr_pct':         p['snapshot']['atr_pct'],
                    'pct_from_52w_high': p['52w']['pct_from_high'],
                    'ma_trend':        p['components']['ma_trend']['detail'],
                    'adx':             p['components']['adx']['detail'],
                }
                for i, p in enumerate(ranking)
            ],
        }

        summary_path = self.technical_dir / 'technical_summary.json'
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        out_paths.append(str(summary_path))

        elapsed  = time.time() - start_time
        log_path = self.log_dir / f"technical_{datetime.now().strftime('%Y-%m-%d')}.txt"
        self._log(f"Done — {len(succeeded)} ok, {len(failed)} failed, {elapsed:.1f}s")
        with open(log_path, 'w') as f:
            f.write('\n'.join([
                'SwingTradeIQ TechnicalAgent',
                f"Date    : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"Duration: {elapsed:.1f}s",
                '',
                *self._log_lines,
            ]))

        print(f"\n{'='*65}")
        print(f"Done in {elapsed:.1f}s | ✅ {len(succeeded)} | ❌ {len(failed)}")
        print(f"{'='*65}")

        return {
            'succeeded':    succeeded,
            'failed':       failed,
            'output_paths': out_paths,
            'elapsed_s':    round(elapsed, 1),
        }
