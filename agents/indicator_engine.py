# SwingTradeIQ — Agent 6/10: IndicatorEngine
# Built in Session 6.
# Handoff: reads data/technical/ + data/fundamental/ + data/validated/
#          → writes data/signals/

import time, json
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

import ta.trend      as ta_trend
import ta.momentum   as ta_mom
import ta.volatility as ta_vol

# ── Constants ─────────────────────────────────────────────────────────────────
PATTERN_LOOKBACK  = 5    # bars to look back for crossover detection
CROSS_LOOKBACK    = 10   # bars for golden/death cross detection
ATR_STOP_MULT     = 2.0  # stop = entry - ATR_STOP_MULT × ATR
ATR_TARGET_MULT_1 = 4.0  # target1 = entry + ATR_TARGET_MULT_1 × ATR  (2:1 R/R)
ATR_TARGET_MULT_2 = 6.0  # target2 = entry + ATR_TARGET_MULT_2 × ATR  (3:1 R/R)

# Combined score weights
W_TECH = 0.60
W_FUND = 0.40

# Signal thresholds (on combined 0-10 score)
THRESH_SWING_BUY = 6.0
THRESH_WATCH     = 4.5

# Conviction thresholds
CONV_HIGH   = 7.5
CONV_MEDIUM = 6.0


class IndicatorEngine:
    """
    Agent 6/10 — SwingTradeIQ Indicator Engine.

    Synthesises outputs from TechnicalAgent and FundamentalAgent, detects
    multi-bar swing-trading patterns, and emits actionable trade signals
    with specific entry / stop-loss / target levels for each ticker.

    This agent does NOT re-score individual indicators — that is TechnicalAgent's
    job. It focuses on:
      (a) combining upstream scores into a single composite,
      (b) detecting patterns that require > 1 bar (crossovers, bounces, squeezes),
      (c) converting the composite into an actionable trade recommendation.

    Patterns detected (last PATTERN_LOOKBACK bars)
    -----------------------------------------------
    MACD_BULL_CROSS   — MACD crossed above signal line
    MACD_BEAR_CROSS   — MACD crossed below signal line
    RSI_OVERSOLD      — RSI < 35 (potential long reversal)
    RSI_OVERBOUGHT    — RSI > 70 (potential short/exit)
    BB_LOWER_TOUCH    — price touched lower Bollinger Band then bounced
    BB_UPPER_TOUCH    — price touching upper Bollinger Band (watch for rejection)
    BB_SQUEEZE        — BB width at 20-bar minimum (breakout loading)
    MA_PULLBACK_BUY   — uptrend (SMA50 > SMA200) + price within 1 ATR of SMA20
    GOLDEN_CROSS      — SMA50 crossed above SMA200 within CROSS_LOOKBACK bars
    DEATH_CROSS       — SMA50 crossed below SMA200 within CROSS_LOOKBACK bars

    Signal logic
    ------------
    Fundamental must PASS for any SWING_BUY signal.
    combined_score = tech_score × 0.60 + fund_score × 0.40

    combined >= 6.0 AND fund PASS → SWING_BUY
    combined >= 4.5              → WATCH
    else                         → NO_TRADE

    Trade levels (SWING_BUY and WATCH only)
    ----------------------------------------
    entry     = last close
    stop_loss = entry − 2.0 × ATR14
    target_1  = entry + 4.0 × ATR14   (2:1 R/R)
    target_2  = entry + 6.0 × ATR14   (3:1 R/R)

    Outputs
    -------
        data/signals/{TICKER}_signal.json  — per-ticker composite signal
        data/signals/signals_summary.json  — ranked actionable summary
        logs/indicator_{DATE}.txt          — run log
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir      = Path(base_dir)
        self.validated_dir = self.base_dir / 'data' / 'validated'
        self.technical_dir = self.base_dir / 'data' / 'technical'
        self.fund_dir      = self.base_dir / 'data' / 'fundamental'
        self.signals_dir   = self.base_dir / 'data' / 'signals'
        self.log_dir       = self.base_dir / 'logs'

        for d in [self.signals_dir, self.log_dir]:
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

    def _load_ohlcv(self, ticker: str) -> pd.DataFrame | None:
        path = self.validated_dir / f"{ticker}_validated.csv"
        if not path.exists():
            return None
        df = pd.read_csv(path, index_col='Date', parse_dates=True)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.sort_index(inplace=True)
        return df

    def _load_technical(self, ticker: str) -> dict | None:
        path = self.technical_dir / f"{ticker}_technical.json"
        return json.load(open(path)) if path.exists() else None

    def _load_fundamental(self, ticker: str) -> dict | None:
        path = self.fund_dir / f"{ticker}_fundamental.json"
        return json.load(open(path)) if path.exists() else None

    # ──────────────────────────────────────────────────────────────────────────
    # Indicator series (re-compute only what's needed for pattern detection)
    # ──────────────────────────────────────────────────────────────────────────

    def _build_series(self, df: pd.DataFrame) -> pd.DataFrame:
        c, h, l = df['Close'], df['High'], df['Low']

        macd_obj         = ta_trend.MACD(c, 26, 12, 9)
        df['macd']       = macd_obj.macd()
        df['macd_sig']   = macd_obj.macd_signal()

        df['rsi']        = ta_mom.RSIIndicator(c, 14).rsi()
        df['sma20']      = ta_trend.SMAIndicator(c, 20).sma_indicator()
        df['sma50']      = ta_trend.SMAIndicator(c, 50).sma_indicator()
        df['sma200']     = ta_trend.SMAIndicator(c, 200).sma_indicator()
        df['atr14']      = ta_vol.AverageTrueRange(h, l, c, 14).average_true_range()

        bb               = ta_vol.BollingerBands(c, 20, 2)
        df['bb_upper']   = bb.bollinger_hband()
        df['bb_lower']   = bb.bollinger_lband()
        df['bb_width']   = bb.bollinger_wband()   # normalised width

        return df

    # ──────────────────────────────────────────────────────────────────────────
    # Pattern detection
    # ──────────────────────────────────────────────────────────────────────────

    def _detect_patterns(self, df: pd.DataFrame) -> list[str]:
        patterns: list[str] = []
        tail   = df.dropna(subset=['macd','macd_sig','rsi','sma20','sma50','sma200']).tail(
                     max(PATTERN_LOOKBACK, CROSS_LOOKBACK) + 1
                 )
        if len(tail) < 3:
            return patterns

        recent = tail.tail(PATTERN_LOOKBACK)
        close  = tail['Close']

        # ── MACD crossovers ───────────────────────────────────────────────────
        macd_above = recent['macd'] > recent['macd_sig']
        macd_below = recent['macd'] < recent['macd_sig']
        if macd_above.any() and not macd_above.all():
            # crossed up within window
            first_above = macd_above.idxmax()
            if not macd_above.iloc[0]:
                patterns.append('MACD_BULL_CROSS')
        if macd_below.any() and not macd_below.all():
            if not macd_below.iloc[0]:
                patterns.append('MACD_BEAR_CROSS')

        # ── RSI extremes ──────────────────────────────────────────────────────
        last_rsi = recent['rsi'].iloc[-1]
        if last_rsi < 35:
            patterns.append('RSI_OVERSOLD')
        if last_rsi > 70:
            patterns.append('RSI_OVERBOUGHT')

        # ── Bollinger touches ──────────────────────────────────────────────────
        # Lower touch: price touched below or at lower band then moved up
        touched_lower = (recent['Close'] <= recent['bb_lower'] * 1.005).any()
        if touched_lower and recent['Close'].iloc[-1] > recent['Close'].iloc[-2]:
            patterns.append('BB_LOWER_TOUCH')

        # Upper touch: last price near or above upper band
        if recent['Close'].iloc[-1] >= recent['bb_upper'].iloc[-1] * 0.995:
            patterns.append('BB_UPPER_TOUCH')

        # BB squeeze: current width at 20-bar minimum
        width_series = df['bb_width'].dropna().tail(20)
        if len(width_series) >= 20:
            if width_series.iloc[-1] <= width_series.min() * 1.05:
                patterns.append('BB_SQUEEZE')

        # ── MA Pullback to SMA20 in uptrend ───────────────────────────────────
        last = tail.iloc[-1]
        if pd.notna(last['sma50']) and pd.notna(last['sma200']):
            in_uptrend = last['sma50'] > last['sma200']
            if in_uptrend and pd.notna(last['atr14']):
                dist_to_sma20 = abs(last['Close'] - last['sma20'])
                if dist_to_sma20 <= last['atr14']:
                    patterns.append('MA_PULLBACK_BUY')

        # ── Golden / Death cross (within CROSS_LOOKBACK bars) ─────────────────
        cross_tail = tail.tail(CROSS_LOOKBACK + 1)
        if len(cross_tail) >= 2:
            prev_cross = cross_tail['sma50'].iloc[:-1] - cross_tail['sma200'].iloc[:-1]
            last_cross = cross_tail['sma50'].iloc[-1]  - cross_tail['sma200'].iloc[-1]
            if (prev_cross < 0).any() and last_cross > 0:
                patterns.append('GOLDEN_CROSS')
            if (prev_cross > 0).any() and last_cross < 0:
                patterns.append('DEATH_CROSS')

        return patterns

    # ──────────────────────────────────────────────────────────────────────────
    # Trade levels
    # ──────────────────────────────────────────────────────────────────────────

    def _trade_levels(self, price: float, atr: float) -> dict:
        stop   = price - ATR_STOP_MULT     * atr
        t1     = price + ATR_TARGET_MULT_1 * atr
        t2     = price + ATR_TARGET_MULT_2 * atr
        risk   = price - stop
        rr1    = (t1 - price) / risk if risk > 0 else None
        rr2    = (t2 - price) / risk if risk > 0 else None
        return {
            'entry':         self._r(price),
            'stop_loss':     self._r(stop),
            'target_1':      self._r(t1),
            'target_2':      self._r(t2),
            'risk_amount':   self._r(risk),
            'risk_pct':      self._r(risk / price * 100),
            'risk_reward_1': self._r(rr1, 1),
            'risk_reward_2': self._r(rr2, 1),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Conviction label
    # ──────────────────────────────────────────────────────────────────────────

    def _conviction(self, combined: float, patterns: list[str]) -> str:
        bullish = {'MACD_BULL_CROSS','RSI_OVERSOLD','BB_LOWER_TOUCH','MA_PULLBACK_BUY','GOLDEN_CROSS'}
        bull_count = len(set(patterns) & bullish)
        if combined >= CONV_HIGH and bull_count >= 2:
            return 'HIGH'
        if combined >= CONV_MEDIUM or bull_count >= 1:
            return 'MEDIUM'
        return 'LOW'

    # ──────────────────────────────────────────────────────────────────────────
    # Per-ticker signal
    # ──────────────────────────────────────────────────────────────────────────

    def _signal_for(self, ticker: str) -> dict:
        tech = self._load_technical(ticker)
        fund = self._load_fundamental(ticker)
        df   = self._load_ohlcv(ticker)

        if not tech or not fund or df is None:
            return {'ticker': ticker, 'signal': 'ERROR', 'reason': 'missing upstream data'}

        df = self._build_series(df)

        tech_score = tech['technical_score']
        fund_score = fund['fundamental_score']
        fund_pass  = fund['pass']
        combined   = round(W_TECH * tech_score + W_FUND * fund_score, 2)

        patterns   = self._detect_patterns(df)

        # Signal decision
        if combined >= THRESH_SWING_BUY and fund_pass:
            signal = 'SWING_BUY'
        elif combined >= THRESH_WATCH:
            signal = 'WATCH'
        else:
            signal = 'NO_TRADE'

        # Trade levels (only if actionable)
        last  = df.iloc[-1]
        price = float(last['Close'])
        atr   = float(last['atr14']) if pd.notna(last['atr14']) else None
        levels = self._trade_levels(price, atr) if atr and signal != 'NO_TRADE' else None

        conviction = self._conviction(combined, patterns) if signal != 'NO_TRADE' else None

        result = {
            'ticker':        ticker,
            'as_of_date':    str(df.index[-1].date()),
            'signal':        signal,
            'conviction':    conviction,
            'patterns':      patterns,
            'combined_score': combined,
            'scores': {
                'technical':   tech_score,
                'fundamental': fund_score,
                'combined':    combined,
            },
            'tech_signal':   tech['signal'],
            'fund_pass':     fund_pass,
            'trade_levels':  levels,
            'snapshot': {
                'price':   self._r(price),
                'atr14':   self._r(atr),
                'atr_pct': self._r(atr / price * 100) if atr else None,
                'rsi14':   self._r(last['rsi'], 1) if pd.notna(last.get('rsi')) else None,
            },
        }

        out = self.signals_dir / f"{ticker}_signal.json"
        with open(out, 'w') as f:
            json.dump(result, f, indent=2, default=str)

        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        start_time      = time.time()
        self._log_lines = []

        tech_files = sorted(self.technical_dir.glob('*_technical.json'))
        tickers    = [f.stem.replace('_technical', '') for f in tech_files]

        if not tickers:
            print("IndicatorEngine: no technical files found in data/technical/")
            return {'succeeded': [], 'failed': [], 'output_paths': [], 'elapsed_s': 0.0}

        self._log(f"IndicatorEngine — {len(tickers)} tickers")
        print(f"{'='*65}")
        print(f"IndicatorEngine.run() — {len(tickers)} tickers")
        print(f"{'='*65}")

        signals   = []
        succeeded = []
        failed    = []
        out_paths = []

        for i, ticker in enumerate(tickers, 1):
            print(f"[{i:>3}/{len(tickers)}] {ticker:<16}", end='', flush=True)
            try:
                sig = self._signal_for(ticker)
                signals.append(sig)
                succeeded.append(ticker)
                out_paths.append(str(self.signals_dir / f"{ticker}_signal.json"))

                pat_str  = ','.join(sig['patterns']) if sig['patterns'] else '—'
                lv_str   = ''
                if sig['trade_levels']:
                    lv = sig['trade_levels']
                    lv_str = f"  entry={lv['entry']}  SL={lv['stop_loss']}  T1={lv['target_1']}  R/R={lv['risk_reward_1']}:1"

                print(
                    f"  {sig['signal']:<12}"
                    f"  [{sig['conviction'] or '—':<6}]"
                    f"  combined={sig['combined_score']:.1f}/10"
                    f"  patterns=[{pat_str}]"
                )
                if lv_str:
                    print(f"{'':>30}{lv_str}")
                self._log(f"{ticker}: {sig['signal']} score={sig['combined_score']} patterns={sig['patterns']}")
            except Exception as exc:
                import traceback
                failed.append({'ticker': ticker, 'reason': str(exc)})
                print(f"  ❌ {exc}")
                self._log(f"ERROR {ticker}: {traceback.format_exc()}")

        # ── Summary ───────────────────────────────────────────────────────────
        ranking = sorted(signals, key=lambda s: s['combined_score'], reverse=True)

        swing_buys = [s for s in signals if s['signal'] == 'SWING_BUY']
        watches    = [s for s in signals if s['signal'] == 'WATCH']
        no_trades  = [s for s in signals if s['signal'] == 'NO_TRADE']

        summary = {
            'run_at':     datetime.now().isoformat(),
            'total':      len(tickers),
            'swing_buys': len(swing_buys),
            'watches':    len(watches),
            'no_trades':  len(no_trades),
            'ranking': [
                {
                    'rank':           i + 1,
                    'ticker':         s['ticker'],
                    'signal':         s['signal'],
                    'conviction':     s['conviction'],
                    'combined_score': s['combined_score'],
                    'tech_score':     s['scores']['technical'],
                    'fund_score':     s['scores']['fundamental'],
                    'patterns':       s['patterns'],
                    'trade_levels':   s['trade_levels'],
                }
                for i, s in enumerate(ranking)
            ],
        }

        summary_path = self.signals_dir / 'signals_summary.json'
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        out_paths.append(str(summary_path))

        # ── Console summary ───────────────────────────────────────────────────
        elapsed = time.time() - start_time
        print(f"\n{'─'*65}")
        print(f"{'Rank':<5} {'Ticker':<14} {'Signal':<13} {'Conv':<7} {'Comb':>5}  {'Tech':>5}  {'Fund':>5}  Patterns")
        print(f"{'─'*65}")
        for row in summary['ranking']:
            print(
                f"{row['rank']:<5} {row['ticker']:<14} {row['signal']:<13}"
                f" {(row['conviction'] or '—'):<7} {row['combined_score']:>5.1f}"
                f"  {row['tech_score']:>5.1f}  {row['fund_score']:>5.1f}"
                f"  {','.join(row['patterns']) if row['patterns'] else '—'}"
            )

        if swing_buys:
            print(f"\n🟢 SWING_BUY candidates: {[s['ticker'] for s in swing_buys]}")
        if watches:
            print(f"🟡 WATCH list: {[s['ticker'] for s in watches]}")
        if no_trades:
            print(f"🔴 NO_TRADE: {[s['ticker'] for s in no_trades]}")

        log_path = self.log_dir / f"indicator_{datetime.now().strftime('%Y-%m-%d')}.txt"
        self._log(f"Done — {len(swing_buys)} SWING_BUY, {len(watches)} WATCH, {len(no_trades)} NO_TRADE, {elapsed:.1f}s")
        with open(log_path, 'w') as f:
            f.write('\n'.join([
                'SwingTradeIQ IndicatorEngine',
                f"Date    : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"Duration: {elapsed:.1f}s",
                '',
                *self._log_lines,
            ]))

        print(f"\n{'='*65}")
        print(f"Done in {elapsed:.1f}s | SWING_BUY={len(swing_buys)} WATCH={len(watches)} NO_TRADE={len(no_trades)} ❌={len(failed)}")
        print(f"{'='*65}")

        return {
            'succeeded':    succeeded,
            'failed':       failed,
            'output_paths': out_paths,
            'elapsed_s':    round(elapsed, 1),
            'swing_buys':   [s['ticker'] for s in swing_buys],
            'watches':      [s['ticker'] for s in watches],
        }
