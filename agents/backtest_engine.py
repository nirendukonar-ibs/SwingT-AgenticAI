# SwingTradeIQ — BacktestEngine
# Vectorised portfolio backtest with 0.2 % per-leg transaction + slippage cost.
# Signal logic mirrors IndicatorEngine (MACD cross + RSI filter + SMA50 trend).
# Outputs: data/backtest/*.json, outputs/backtest_*.html, outputs/backtest_*.csv

import csv as _csv
import io
import json
import base64
import time
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import yaml
import yfinance as yf
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, MACD as TaMACD
from ta.volatility import AverageTrueRange

# ── Cost model ────────────────────────────────────────────────────────────────
COST_PER_LEG   = 0.002   # 0.2 % per leg; round-trip = 0.4 %
# ── Position rules ────────────────────────────────────────────────────────────
MAX_POSITION_PCT   = 0.20  # max 20 % of current equity per position
MAX_CONCURRENT     = 4     # maximum simultaneous open positions
MAX_HOLD_DAYS      = 20    # trading days before forced exit (TIMEOUT)
# ── ATR multiples (mirrors IndicatorEngine) ───────────────────────────────────
ATR_STOP_MULT  = 2.0
ATR_T1_MULT    = 4.0
ATR_T2_MULT    = 6.0
# ── Signal filters ────────────────────────────────────────────────────────────
RSI_LO         = 30
RSI_HI         = 70
MIN_ATR_PCT    = 0.005    # skip signals where ATR/price < 0.5 % (too quiet)
# ── Data warmup ───────────────────────────────────────────────────────────────
WARMUP_CAL_DAYS = 300     # calendar days before start_date for indicator burn-in
BENCHMARK       = '^NSEI' # NIFTY 50


class BacktestEngine:
    """
    Portfolio backtest engine for SwingTradeIQ.

    Signal logic
    ------------
    Entry (generated at close, executed at next-day open):
      • MACD(12,26,9) line crosses above signal line
      • RSI14 in [30, 70]  — not oversold or overbought
      • Close > SMA50       — confirmed uptrend

    Exit priority (checked each subsequent bar):
      1. Gap-down open below stop  → exit at open
      2. Intraday Low  < stop_loss → exit at stop_loss
      3. Intraday High > target_1  → exit at target_1
      4. Hold count >= MAX_HOLD_DAYS → exit at close (TIMEOUT)
      5. End of backtest period    → exit at last close (END_OF_PERIOD)

    Position sizing
    ---------------
    • Allocate min(MAX_POSITION_PCT × current_equity, available_cash)
    • Hard cap: MAX_CONCURRENT open positions at any time

    Cost model
    ----------
    Entry effective price = open  × (1 + COST_PER_LEG)   [0.2 % cost]
    Exit effective price  = price × (1 - COST_PER_LEG)   [0.2 % cost]
    Round-trip cost = 0.4 % of notional — covers brokerage, STT, NSE
    charges, and slippage.
    """

    def __init__(
        self,
        base_dir:   str | Path,
        start_date: str,          # 'YYYY-MM-DD'
        end_date:   str,          # 'YYYY-MM-DD'
        capital:    float | None = None,
        tickers:    list[str] | None = None,
    ):
        self.base_dir   = Path(base_dir)
        self.start      = pd.Timestamp(start_date)
        self.end        = pd.Timestamp(end_date)
        self.cfg        = yaml.safe_load(open(self.base_dir / 'config.yaml'))
        self.capital    = capital or self.cfg['portfolio']['total_capital']
        self.tickers    = tickers or self._default_tickers()
        self.bt_dir     = self.base_dir / 'data' / 'backtest'
        self.out_dir    = self.base_dir / 'outputs'
        self.log_dir    = self.base_dir / 'logs'
        for d in [self.bt_dir, self.out_dir, self.log_dir]:
            d.mkdir(parents=True, exist_ok=True)
        self._log_lines: list[str] = []

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self._log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _default_tickers(self) -> list[str]:
        meta = self.base_dir / 'data' / 'meta' / 'universe_meta.json'
        if meta.exists():
            m = json.load(open(meta))
            return [t.replace('.NS', '') for t in m]
        return ['INFY', 'HDFCBANK', 'TCS', 'BAJAJ-AUTO']

    # ── Data ─────────────────────────────────────────────────────────────────

    def _fetch(self, ticker: str) -> pd.DataFrame | None:
        fetch_from = self.start - timedelta(days=WARMUP_CAL_DAYS)
        sym        = f"{ticker}.NS" if not ticker.endswith('.NS') else ticker
        self._log(f"Fetching {sym} {fetch_from.date()} → {self.end.date()}")
        print(f"    fetching {sym} ...", end=' ', flush=True)
        try:
            df = yf.download(
                sym,
                start=fetch_from.strftime('%Y-%m-%d'),
                end=(self.end + timedelta(days=1)).strftime('%Y-%m-%d'),
                auto_adjust=True,
                progress=False,
            )
            if df.empty or len(df) < 60:
                print(f"❌  too little data ({len(df)} rows)")
                return None
            # Flatten multi-level columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.index = pd.to_datetime(df.index)
            df = df[['Open','High','Low','Close','Volume']].copy()
            df.dropna(subset=['Close'], inplace=True)
            print(f"✅  {len(df)} rows  ({df.index[0].date()} → {df.index[-1].date()})")
            return df
        except Exception as e:
            print(f"❌  {e}")
            return None

    def _fetch_benchmark(self) -> pd.Series | None:
        fetch_from = self.start - timedelta(days=5)
        try:
            df = yf.download(
                BENCHMARK,
                start=fetch_from.strftime('%Y-%m-%d'),
                end=(self.end + timedelta(days=1)).strftime('%Y-%m-%d'),
                auto_adjust=True,
                progress=False,
            )
            if df.empty:
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.index = pd.to_datetime(df.index)
            return df['Close'].dropna()
        except Exception:
            return None

    # ── Indicators + signals ──────────────────────────────────────────────────

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        c = df['Close']
        df['sma20']  = SMAIndicator(c, window=20).sma_indicator()
        df['sma50']  = SMAIndicator(c, window=50).sma_indicator()
        df['sma200'] = SMAIndicator(c, window=200).sma_indicator()
        macd_obj     = TaMACD(c, window_slow=26, window_fast=12, window_sign=9)
        df['macd']   = macd_obj.macd()
        df['macd_s'] = macd_obj.macd_signal()
        df['rsi']    = RSIIndicator(c, window=14).rsi()
        atr_obj      = AverageTrueRange(df['High'], df['Low'], c, window=14)
        df['atr']    = atr_obj.average_true_range()
        df['atr_pct']= df['atr'] / c
        return df

    def _add_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        prev = df.shift(1)
        # MACD bull cross: line crosses above signal
        macd_cross   = (df['macd'] > df['macd_s']) & (prev['macd'] <= prev['macd_s'])
        rsi_ok       = df['rsi'].between(RSI_LO, RSI_HI)
        trend_ok     = df['Close'] > df['sma50']
        atr_ok       = df['atr_pct'] > MIN_ATR_PCT
        no_nan       = df[['sma50','macd','macd_s','rsi','atr']].notna().all(axis=1)
        df['signal'] = macd_cross & rsi_ok & trend_ok & atr_ok & no_nan
        return df

    # ── Portfolio simulation ──────────────────────────────────────────────────

    def _simulate(self, data: dict[str, pd.DataFrame]) -> tuple[list, pd.Series]:
        """
        Walk-forward portfolio simulation.
        Returns (trade_log, equity_series indexed by date).
        """
        # Union of trading dates within [start, end]
        all_dates = sorted(set.union(*[set(df.loc[self.start:self.end].index)
                                       for df in data.values()]))
        if not all_dates:
            return [], pd.Series(dtype=float)

        cash            = float(self.capital)
        positions: dict = {}        # ticker → position dict
        pending: dict   = {}        # tickers with entry signals (execute next bar)
        trade_log       = []
        equity_curve    = {}

        for i, date in enumerate(all_dates):

            # ── Execute pending entries at today's open ────────────────────
            if pending and len(positions) < MAX_CONCURRENT:
                # Sort by ATR% desc (higher volatility → stronger signal)
                ordered = sorted(pending.items(),
                                 key=lambda kv: kv[1]['atr_pct'], reverse=True)
                for ticker, sig in ordered:
                    if len(positions) >= MAX_CONCURRENT:
                        break
                    if ticker in positions:
                        continue
                    df = data[ticker]
                    if date not in df.index:
                        continue
                    open_px  = float(df.loc[date, 'Open'])
                    entry_px = open_px * (1 + COST_PER_LEG)
                    alloc    = min(cash * MAX_POSITION_PCT, cash)
                    shares   = int(alloc / entry_px)
                    if shares < 1:
                        continue
                    cost = shares * entry_px
                    cash -= cost

                    atr = sig['atr']
                    positions[ticker] = {
                        'shares':     shares,
                        'entry_price':entry_px,
                        'entry_open': open_px,
                        'stop':       entry_px - ATR_STOP_MULT  * atr,
                        'target1':    entry_px + ATR_T1_MULT    * atr,
                        'target2':    entry_px + ATR_T2_MULT    * atr,
                        'entry_date': date,
                        'atr':        atr,
                        'hold_count': 0,
                    }
                pending = {}

            # ── Check exits on open positions ──────────────────────────────
            closed = []
            for ticker, pos in positions.items():
                df = data[ticker]
                if date not in df.index:
                    continue
                row     = df.loc[date]
                hi, lo  = float(row['High']), float(row['Low'])
                op, cl  = float(row['Open']), float(row['Close'])
                pos['hold_count'] += 1

                exit_px = reason = None

                if op < pos['stop']:                         # gap-down
                    exit_px = op * (1 - COST_PER_LEG)
                    reason  = 'STOP_GAPDOWN'
                elif lo < pos['stop']:                       # intraday stop
                    exit_px = pos['stop'] * (1 - COST_PER_LEG)
                    reason  = 'STOP'
                elif hi > pos['target1']:                    # target 1
                    exit_px = pos['target1'] * (1 - COST_PER_LEG)
                    reason  = 'TARGET1'
                elif pos['hold_count'] >= MAX_HOLD_DAYS:     # timeout
                    exit_px = cl * (1 - COST_PER_LEG)
                    reason  = 'TIMEOUT'

                if exit_px is not None:
                    proceeds = pos['shares'] * exit_px
                    cash += proceeds
                    pnl  = proceeds - pos['shares'] * pos['entry_price']
                    risk = pos['shares'] * (pos['entry_price'] - pos['stop'])
                    trade_log.append({
                        'ticker':       ticker,
                        'entry_date':   pos['entry_date'].strftime('%Y-%m-%d'),
                        'exit_date':    date.strftime('%Y-%m-%d'),
                        'entry_price':  round(pos['entry_price'],  4),
                        'exit_price':   round(exit_px,             4),
                        'stop_loss':    round(pos['stop'],         4),
                        'target1':      round(pos['target1'],      4),
                        'target2':      round(pos['target2'],      4),
                        'shares':       pos['shares'],
                        'pnl_inr':      round(pnl,                 2),
                        'pnl_pct':      round(pnl / (pos['shares'] * pos['entry_price']) * 100, 3),
                        'r_multiple':   round(pnl / risk, 3) if risk > 0 else 0.0,
                        'hold_days':    pos['hold_count'],
                        'exit_reason':  reason,
                        'atr_at_entry': round(pos['atr'], 4),
                    })
                    closed.append(ticker)

            for t in closed:
                del positions[t]

            # ── Check new entry signals (enter tomorrow) ───────────────────
            for ticker, df in data.items():
                if ticker in positions or ticker in pending:
                    continue
                if date not in df.index:
                    continue
                if not df.loc[date, 'signal']:
                    continue
                pending[ticker] = {
                    'atr':     float(df.loc[date, 'atr']),
                    'atr_pct': float(df.loc[date, 'atr_pct']),
                }

            # ── Daily equity = cash + mark-to-market ──────────────────────
            mtm = sum(
                pos['shares'] * float(data[t].loc[date, 'Close'])
                for t, pos in positions.items()
                if date in data[t].index
            )
            equity_curve[date] = cash + mtm

        # ── Force-close remaining positions at end of period ──────────────
        last = all_dates[-1]
        for ticker, pos in positions.items():
            df      = data[ticker]
            avail   = df.index[df.index <= last]
            if avail.empty:
                continue
            ld       = avail[-1]
            exit_px  = float(df.loc[ld, 'Close']) * (1 - COST_PER_LEG)
            proceeds = pos['shares'] * exit_px
            cash    += proceeds
            pnl      = proceeds - pos['shares'] * pos['entry_price']
            risk     = pos['shares'] * (pos['entry_price'] - pos['stop'])
            trade_log.append({
                'ticker':       ticker,
                'entry_date':   pos['entry_date'].strftime('%Y-%m-%d'),
                'exit_date':    ld.strftime('%Y-%m-%d'),
                'entry_price':  round(pos['entry_price'], 4),
                'exit_price':   round(exit_px,            4),
                'stop_loss':    round(pos['stop'],        4),
                'target1':      round(pos['target1'],     4),
                'target2':      round(pos['target2'],     4),
                'shares':       pos['shares'],
                'pnl_inr':      round(pnl,                2),
                'pnl_pct':      round(pnl / (pos['shares'] * pos['entry_price']) * 100, 3),
                'r_multiple':   round(pnl / risk, 3) if risk > 0 else 0.0,
                'hold_days':    pos['hold_count'],
                'exit_reason':  'END_OF_PERIOD',
                'atr_at_entry': round(pos['atr'], 4),
            })

        equity_curve[last] = cash
        return trade_log, pd.Series(equity_curve).sort_index()

    # ── Metrics ───────────────────────────────────────────────────────────────

    def _metrics(self, trades: list, eq: pd.Series) -> dict:
        if eq.empty or self.capital == 0:
            return {'error': 'empty equity curve'}

        eq = eq.sort_index()
        daily_ret = eq.pct_change().dropna()
        final     = float(eq.iloc[-1])

        # Returns
        total_ret = (final / self.capital - 1) * 100
        years     = max((self.end - self.start).days / 365.25, 1/252)
        cagr      = ((final / self.capital) ** (1 / years) - 1) * 100

        # Risk
        sharpe  = (float(daily_ret.mean()) / float(daily_ret.std())
                   * np.sqrt(252)) if daily_ret.std() > 0 else 0.0
        roll_max = eq.cummax()
        dd       = (eq - roll_max) / roll_max * 100
        max_dd   = float(dd.min())
        calmar   = cagr / abs(max_dd) if max_dd != 0 else 0.0

        # Trades
        wins     = [t for t in trades if t['pnl_inr'] > 0]
        losses   = [t for t in trades if t['pnl_inr'] <= 0]
        n        = len(trades)
        gp       = sum(t['pnl_inr'] for t in wins)
        gl       = abs(sum(t['pnl_inr'] for t in losses))

        per_tk: dict[str, dict] = {}
        for t in trades:
            tk = t['ticker']
            if tk not in per_tk:
                per_tk[tk] = {'trades': 0, 'wins': 0, 'pnl': 0.0}
            per_tk[tk]['trades'] += 1
            per_tk[tk]['wins']   += 1 if t['pnl_inr'] > 0 else 0
            per_tk[tk]['pnl']    += t['pnl_inr']
        for tk in per_tk:
            per_tk[tk]['win_rate'] = round(per_tk[tk]['wins'] / per_tk[tk]['trades'] * 100, 1)
            per_tk[tk]['pnl']     = round(per_tk[tk]['pnl'], 2)

        exit_counts: dict[str, int] = {}
        for t in trades:
            exit_counts[t['exit_reason']] = exit_counts.get(t['exit_reason'], 0) + 1

        return {
            'period':             f"{self.start.date()} → {self.end.date()}",
            'capital_inr':        self.capital,
            'final_equity_inr':   round(final, 2),
            'total_return_pct':   round(total_ret,  2),
            'cagr_pct':           round(cagr,       2),
            'sharpe_ratio':       round(sharpe,      3),
            'max_drawdown_pct':   round(max_dd,      2),
            'calmar_ratio':       round(calmar,       3),
            'total_trades':       n,
            'winning_trades':     len(wins),
            'losing_trades':      len(losses),
            'win_rate_pct':       round(len(wins)/n*100, 1) if n else 0,
            'profit_factor':      round(gp/gl, 3)           if gl else None,
            'gross_profit_inr':   round(gp,  2),
            'gross_loss_inr':     round(gl,  2),
            'avg_win_inr':        round(np.mean([t['pnl_inr'] for t in wins]),   2) if wins   else 0,
            'avg_loss_inr':       round(np.mean([t['pnl_inr'] for t in losses]), 2) if losses else 0,
            'avg_r_multiple':     round(np.mean([t['r_multiple'] for t in trades]), 3) if trades else 0,
            'avg_hold_days':      round(np.mean([t['hold_days'] for t in trades]), 1) if trades else 0,
            'cost_model':         f"{COST_PER_LEG*100:.1f}% per leg / {COST_PER_LEG*200:.1f}% round-trip",
            'exit_reasons':       exit_counts,
            'per_ticker':         per_tk,
        }

    def _benchmark_metrics(self, bench: pd.Series | None) -> dict:
        if bench is None or bench.empty:
            return {}
        b = bench.loc[self.start:self.end].dropna()
        if len(b) < 2:
            return {}
        total = (float(b.iloc[-1]) / float(b.iloc[0]) - 1) * 100
        years = max((self.end - self.start).days / 365.25, 1/252)
        cagr  = ((float(b.iloc[-1]) / float(b.iloc[0])) ** (1/years) - 1) * 100
        dr    = b.pct_change().dropna()
        sh    = float(dr.mean()) / float(dr.std()) * np.sqrt(252) if dr.std() > 0 else 0
        rm    = b.cummax()
        dd    = ((b - rm) / rm * 100).min()
        return {
            'total_return_pct': round(total, 2),
            'cagr_pct':         round(cagr,  2),
            'sharpe_ratio':     round(sh,    3),
            'max_drawdown_pct': round(float(dd), 2),
        }

    # ── Chart ─────────────────────────────────────────────────────────────────

    def _equity_chart_b64(self, eq: pd.Series, bench: pd.Series | None) -> str:
        fig, ax = plt.subplots(figsize=(14, 4))
        fig.patch.set_facecolor('#0f1117')
        ax.set_facecolor('#151e2d')

        base   = float(eq.iloc[0])
        norm   = eq / base * 100
        ax.plot(norm.index, norm.values, color='#60a5fa', lw=1.8, label='Portfolio')

        # Shade drawdown area
        roll_max = norm.cummax()
        ax.fill_between(norm.index, norm.values, roll_max.values,
                        alpha=0.25, color='#f87171', label='Drawdown')

        if bench is not None:
            b = bench.reindex(eq.index, method='ffill').dropna()
            if len(b) > 1:
                nb = b / float(b.iloc[0]) * 100
                ax.plot(nb.index, nb.values, color='#64748b',
                        lw=1.2, ls='--', label='NIFTY50 B&H')

        ax.axhline(100, color='#334155', lw=0.8, ls=':')
        ax.set_ylabel('Indexed (base=100)', color='#94a3b8', fontsize=9)
        for sp in ['top', 'right']:
            ax.spines[sp].set_visible(False)
        for sp in ['bottom', 'left']:
            ax.spines[sp].set_color('#334155')
        ax.tick_params(colors='#94a3b8', labelsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha='right')
        ax.grid(True, color='#1e293b', lw=0.5, axis='y')
        ax.legend(facecolor='#1e293b', edgecolor='#334155',
                  labelcolor='#94a3b8', fontsize=8)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=110, bbox_inches='tight',
                    facecolor='#0f1117')
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()

    # ── HTML report ───────────────────────────────────────────────────────────

    def _build_html(self, m: dict, trades: list, eq: pd.Series,
                    bench: pd.Series | None, bm: dict) -> str:

        chart_b64 = self._equity_chart_b64(eq, bench)
        chart_tag = f'<img src="data:image/png;base64,{chart_b64}" style="width:100%;border-radius:8px"/>'

        def kpi(label, val, cls='', suffix=''):
            return (f'<div class="kpi"><div class="label">{label}</div>'
                    f'<div class="value {cls}">{val}{suffix}</div></div>')

        def _c(v, pos_good=True):
            if v is None: return 'muted'
            try:
                f = float(v)
                if pos_good: return 'green' if f > 0 else ('red' if f < 0 else 'muted')
                return 'red' if f > 0 else ('green' if f < 0 else 'muted')
            except Exception: return 'muted'

        def _f(v, d=2, prefix='', suffix=''):
            if v is None: return '—'
            try: return f"{prefix}{float(v):,.{d}f}{suffix}"
            except: return str(v)

        kpis = ''.join([
            kpi('Period',       m.get('period',''),   'blue'),
            kpi('Total Return', _f(m.get('total_return_pct'),1,'','%'), _c(m.get('total_return_pct'))),
            kpi('CAGR',         _f(m.get('cagr_pct'),1,'','%'),        _c(m.get('cagr_pct'))),
            kpi('Sharpe',       _f(m.get('sharpe_ratio'),3),            _c(m.get('sharpe_ratio'))),
            kpi('Max Drawdown', _f(m.get('max_drawdown_pct'),1,'','%'),'red'),
            kpi('Win Rate',     _f(m.get('win_rate_pct'),1,'','%'),     _c(m.get('win_rate_pct'), False)),
            kpi('Profit Factor',_f(m.get('profit_factor'),3),           _c(m.get('profit_factor'))),
            kpi('Trades',       str(m.get('total_trades',0)),           'blue'),
        ])

        # Benchmark comparison table
        bm_rows = ''
        metrics_cmp = [
            ('Total Return %',  'total_return_pct', True),
            ('CAGR %',          'cagr_pct',         True),
            ('Sharpe',          'sharpe_ratio',      True),
            ('Max Drawdown %',  'max_drawdown_pct',  False),
        ]
        for label, key, pg in metrics_cmp:
            pv = m.get(key); bv = bm.get(key)
            edge_cls = _c((pv or 0) - (bv or 0), pg) if (pv is not None and bv is not None) else 'muted'
            bm_rows += (f'<tr><td>{label}</td>'
                        f'<td class="{_c(pv,pg)}">{_f(pv)}</td>'
                        f'<td class="{_c(bv,pg)}">{_f(bv)}</td>'
                        f'<td class="{edge_cls}">{_f((pv or 0)-(bv or 0)) if pv is not None and bv is not None else "—"}</td>'
                        f'</tr>')

        # Per-ticker table
        tk_rows = ''
        for tk, tv in m.get('per_ticker', {}).items():
            tk_rows += (f'<tr><td>{tk}</td><td>{tv["trades"]}</td>'
                        f'<td class="{_c(tv["win_rate"]-50)}">{tv["win_rate"]}%</td>'
                        f'<td class="{_c(tv["pnl"])}">₹{tv["pnl"]:,.0f}</td></tr>')

        # Exit reason table
        er_rows = ''
        total_t = max(m.get('total_trades', 1), 1)
        for reason, cnt in m.get('exit_reasons', {}).items():
            er_rows += f'<tr><td>{reason}</td><td>{cnt}</td><td>{cnt/total_t*100:.1f}%</td></tr>'

        # Trade log table (last 50)
        shown = trades[-50:] if len(trades) > 50 else trades
        tr_rows = ''
        for t in shown:
            pnl_cls = 'green' if t['pnl_inr'] > 0 else 'red'
            r_cls   = 'green' if t['r_multiple'] > 0 else 'red'
            tr_rows += (
                f'<tr><td>{t["ticker"]}</td>'
                f'<td>{t["entry_date"]}</td><td>{t["exit_date"]}</td>'
                f'<td>₹{_f(t["entry_price"])}</td><td>₹{_f(t["exit_price"])}</td>'
                f'<td class="red">₹{_f(t["stop_loss"])}</td>'
                f'<td class="green">₹{_f(t["target1"])}</td>'
                f'<td>{t["shares"]}</td>'
                f'<td class="{pnl_cls}">₹{t["pnl_inr"]:,.0f}</td>'
                f'<td class="{pnl_cls}">{t["pnl_pct"]:+.2f}%</td>'
                f'<td class="{r_cls}">{t["r_multiple"]:+.2f}R</td>'
                f'<td>{t["hold_days"]}d</td>'
                f'<td>{t["exit_reason"]}</td></tr>'
            )

        note = f' (showing last {len(shown)} of {len(trades)})' if len(trades) > 50 else ''

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>SwingTradeIQ Backtest — {self.start.date()} to {self.end.date()}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',Arial,sans-serif;background:#0f1117;color:#e2e8f0;line-height:1.55}}
.wrap{{max-width:1200px;margin:0 auto;padding:32px 24px}}
h1{{font-size:1.85rem;font-weight:700;color:#f0f4ff}}
h2{{font-size:1rem;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:.08em;
    margin:36px 0 12px;border-bottom:1px solid #1e293b;padding-bottom:6px}}
.meta{{color:#64748b;font-size:.85rem;margin-top:4px}}
.kpi-row{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px}}
.kpi{{background:#1e293b;border-radius:8px;padding:14px 18px;flex:1;min-width:130px}}
.kpi .label{{font-size:.72rem;color:#64748b;text-transform:uppercase;letter-spacing:.06em}}
.kpi .value{{font-size:1.4rem;font-weight:700;margin-top:4px}}
.green{{color:#4ade80}}.red{{color:#f87171}}.amber{{color:#fbbf24}}
.blue{{color:#60a5fa}}.muted{{color:#94a3b8}}
table{{width:100%;border-collapse:collapse;font-size:.84rem;margin-top:8px}}
th{{background:#1e293b;color:#94a3b8;text-align:left;padding:8px 10px;
    font-weight:600;font-size:.74rem;text-transform:uppercase;letter-spacing:.04em}}
td{{padding:8px 10px;border-bottom:1px solid #1e293b}}
tr:hover td{{background:#1a2235}}
.card{{background:#151e2d;border-radius:10px;padding:18px 22px;margin-bottom:14px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
.footer{{color:#334155;font-size:.74rem;margin-top:48px;text-align:center}}
</style>
</head>
<body>
<div class="wrap">
<h1>SwingTradeIQ — Backtest Report</h1>
<p class="meta">{m.get('period','')} &nbsp;|&nbsp;
Capital ₹{self.capital:,.0f} &nbsp;|&nbsp;
Universe: {', '.join(self.tickers)} &nbsp;|&nbsp;
Cost: {COST_PER_LEG*100:.1f}% per leg</p>

<h2>Performance Summary</h2>
<div class="kpi-row">{kpis}</div>

<h2>Equity Curve vs NIFTY50 Buy &amp; Hold</h2>
<div class="card">{chart_tag}</div>

<h2>Strategy vs Benchmark</h2>
<div class="card">
<table style="max-width:520px">
<tr><th>Metric</th><th>Strategy</th><th>NIFTY50 B&amp;H</th><th>Edge</th></tr>
{bm_rows}
</table></div>

<h2>Risk Detail</h2>
<div class="card">
<div class="grid2">
<table>
<tr><td>Final equity</td><td class="blue">₹{_f(m.get('final_equity_inr'),0)}</td></tr>
<tr><td>Total return</td><td class="{_c(m.get('total_return_pct'))}">{_f(m.get('total_return_pct'),1)}%</td></tr>
<tr><td>CAGR</td><td class="{_c(m.get('cagr_pct'))}">{_f(m.get('cagr_pct'),1)}%</td></tr>
<tr><td>Sharpe ratio</td><td class="{_c(m.get('sharpe_ratio'))}">{_f(m.get('sharpe_ratio'),3)}</td></tr>
<tr><td>Calmar ratio</td><td class="{_c(m.get('calmar_ratio'))}">{_f(m.get('calmar_ratio'),3)}</td></tr>
<tr><td>Max drawdown</td><td class="red">{_f(m.get('max_drawdown_pct'),1)}%</td></tr>
</table>
<table>
<tr><td>Total trades</td><td class="blue">{m.get('total_trades',0)}</td></tr>
<tr><td>Win rate</td><td class="{_c(m.get('win_rate_pct',0)-50)}">{_f(m.get('win_rate_pct'),1)}%</td></tr>
<tr><td>Profit factor</td><td class="{_c(m.get('profit_factor'))}">{_f(m.get('profit_factor'),3)}</td></tr>
<tr><td>Avg win ₹</td><td class="green">₹{_f(m.get('avg_win_inr'),0)}</td></tr>
<tr><td>Avg loss ₹</td><td class="red">₹{_f(m.get('avg_loss_inr'),0)}</td></tr>
<tr><td>Avg R-multiple</td><td class="{_c(m.get('avg_r_multiple'))}">{_f(m.get('avg_r_multiple'),3)}R</td></tr>
<tr><td>Avg hold (days)</td><td class="muted">{_f(m.get('avg_hold_days'),1)}</td></tr>
<tr><td>Cost model</td><td class="muted">{m.get('cost_model','')}</td></tr>
</table>
</div></div>

<h2>Per-Ticker Summary</h2>
<div class="card"><table style="max-width:460px">
<tr><th>Ticker</th><th>Trades</th><th>Win %</th><th>Net P&amp;L ₹</th></tr>
{tk_rows}</table></div>

<h2>Exit Reason Breakdown</h2>
<div class="card"><table style="max-width:340px">
<tr><th>Reason</th><th>Count</th><th>%</th></tr>
{er_rows}</table></div>

<h2>Trade Log{note}</h2>
<div class="card">
<table>
<tr><th>Ticker</th><th>Entry</th><th>Exit</th><th>Entry ₹</th><th>Exit ₹</th>
    <th>Stop ₹</th><th>T1 ₹</th><th>Qty</th>
    <th>P&amp;L ₹</th><th>P&amp;L %</th><th>R</th><th>Hold</th><th>Reason</th></tr>
{tr_rows}
</table></div>

<div class="footer">SwingTradeIQ Backtest &nbsp;|&nbsp; {datetime.now().strftime('%Y-%m-%d %H:%M')} &nbsp;|&nbsp; IBS India MBA — Advanced Business Analytics</div>
</div>
</body>
</html>"""

    # ── Console output ────────────────────────────────────────────────────────

    def _print_results(self, m: dict, bm: dict, trades: list) -> None:
        def f(v, d=2, suf=''):
            if v is None: return '—'
            try: return f"{float(v):,.{d}f}{suf}"
            except Exception: return str(v)
        print(f"\n{'─'*60}")
        print(f"  BACKTEST RESULTS  |  {m.get('period','')}")
        print(f"{'─'*60}")
        print(f"  {'Metric':<28} {'Strategy':>12}  {'NIFTY50':>10}")
        print(f"  {'─'*55}")
        rows = [
            ('Total Return %',  m.get('total_return_pct'), bm.get('total_return_pct')),
            ('CAGR %',          m.get('cagr_pct'),         bm.get('cagr_pct')),
            ('Sharpe Ratio',    m.get('sharpe_ratio'),     bm.get('sharpe_ratio')),
            ('Max Drawdown %',  m.get('max_drawdown_pct'), bm.get('max_drawdown_pct')),
            ('Calmar Ratio',    m.get('calmar_ratio'),     None),
        ]
        for label, sv, bv in rows:
            bv_s = f(bv) if bv is not None else '—'
            print(f"  {label:<28} {f(sv):>12}  {bv_s:>10}")
        print(f"\n  Trades: {m.get('total_trades',0)}  "
              f"| Win rate: {f(m.get('win_rate_pct'),1)}%  "
              f"| Profit factor: {f(m.get('profit_factor'),3)}  "
              f"| Avg R: {f(m.get('avg_r_multiple'),3)}")
        print(f"  Avg hold: {f(m.get('avg_hold_days'),1)} days  "
              f"| Cost: {m.get('cost_model','')}")
        print(f"\n  Exit reasons: {m.get('exit_reasons',{})}")
        print(f"\n  Per-ticker:")
        for tk, tv in m.get('per_ticker', {}).items():
            print(f"    {tk:<14} trades={tv['trades']}  win={tv['win_rate']}%  pnl=₹{tv['pnl']:,.0f}")

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self) -> dict:
        t0 = time.time()
        self._log_lines = []
        slug  = f"{self.start.date()}_{self.end.date()}"
        date  = datetime.now().strftime('%Y-%m-%d')

        print(f"\n{'═'*60}")
        print(f"  BacktestEngine  |  {self.start.date()} → {self.end.date()}")
        print(f"  Capital: ₹{self.capital:,.0f}  |  Tickers: {self.tickers}")
        print(f"  Cost: {COST_PER_LEG*100:.1f}% per leg  |  Max concurrent: {MAX_CONCURRENT}")
        print(f"{'═'*60}")

        # 1. Fetch data
        print(f"\n[1/5] Fetching OHLCV data ...")
        raw: dict[str, pd.DataFrame] = {}
        for tk in self.tickers:
            df = self._fetch(tk)
            if df is not None and len(df) >= 60:
                raw[tk] = df

        if not raw:
            print("ERROR: no data fetched — aborting backtest")
            return {'succeeded': [], 'failed': self.tickers, 'output_paths': [], 'elapsed_s': 0.0}

        # 2. Compute indicators and signals
        print(f"\n[2/5] Computing indicators + signals ...")
        data: dict[str, pd.DataFrame] = {}
        signal_counts = {}
        for tk, df in raw.items():
            df = self._add_indicators(df)
            df = self._add_signals(df)
            # Slice to backtest window only (after warmup)
            data[tk]         = df.loc[self.start:self.end]
            n_sig            = int(df.loc[self.start:self.end, 'signal'].sum())
            signal_counts[tk]= n_sig
            self._log(f"{tk}: {n_sig} signals in window")
            print(f"    {tk:<14}  indicators OK  |  signals in window: {n_sig}")

        # 3. Fetch benchmark
        print(f"\n[3/5] Fetching benchmark ({BENCHMARK}) ...")
        bench = self._fetch_benchmark()
        bm    = self._benchmark_metrics(bench)
        if bm:
            print(f"    NIFTY50: ret={bm.get('total_return_pct')}%  sharpe={bm.get('sharpe_ratio')}")
        else:
            print("    Could not fetch NIFTY50 benchmark")

        # 4. Run portfolio simulation
        print(f"\n[4/5] Running portfolio simulation ...")
        trades, eq = self._simulate(data)
        print(f"    Simulation complete: {len(trades)} trades  |  {len(eq)} equity observations")

        # 5. Compute metrics and write outputs
        print(f"\n[5/5] Computing metrics and writing outputs ...")
        m     = self._metrics(trades, eq)
        self._print_results(m, bm, trades)

        out_paths = []

        # JSON results
        results = {
            'run_at':    datetime.now().isoformat(),
            'config':    {'start': str(self.start.date()), 'end': str(self.end.date()),
                          'capital': self.capital, 'tickers': self.tickers,
                          'cost_pct_per_leg': COST_PER_LEG,
                          'max_concurrent': MAX_CONCURRENT,
                          'max_hold_days': MAX_HOLD_DAYS},
            'metrics':   m,
            'benchmark': bm,
            'trades':    trades,
        }
        jp = self.bt_dir / f"backtest_{slug}.json"
        with open(jp, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        out_paths.append(str(jp))
        print(f"    JSON  → {jp}  ({jp.stat().st_size/1024:.1f} KB)")

        # CSV trade log
        if trades:
            cp = self.out_dir / f"backtest_{slug}_trades.csv"
            fields = ['ticker','entry_date','exit_date','entry_price','exit_price',
                      'stop_loss','target1','target2','shares','pnl_inr','pnl_pct',
                      'r_multiple','hold_days','exit_reason','atr_at_entry']
            with open(cp, 'w', newline='') as f:
                w = _csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
                w.writeheader()
                w.writerows(trades)
            out_paths.append(str(cp))
            print(f"    CSV   → {cp}  ({cp.stat().st_size} bytes)")

        # HTML report
        if not eq.empty:
            hp = self.out_dir / f"backtest_{slug}.html"
            html = self._build_html(m, trades, eq, bench, bm)
            hp.write_text(html, encoding='utf-8')
            out_paths.append(str(hp))
            print(f"    HTML  → {hp}  ({hp.stat().st_size/1024:.1f} KB)")

        # Log
        elapsed = time.time() - t0
        self._log(f"Done — {elapsed:.1f}s  trades={len(trades)}")
        lp = self.log_dir / f"backtest_{date}.txt"
        with open(lp, 'w') as f:
            f.write('\n'.join(['SwingTradeIQ BacktestEngine',
                               f"Period: {slug}", f"Duration: {elapsed:.1f}s",
                               '', *self._log_lines]))

        print(f"\n{'═'*60}")
        print(f"  Done in {elapsed:.1f}s  |  {len(trades)} trades  |  {len(out_paths)} files written")
        print(f"{'═'*60}")

        return {
            'succeeded':    list(raw.keys()),
            'failed':       [t for t in self.tickers if t not in raw],
            'metrics':      m,
            'trades':       len(trades),
            'output_paths': out_paths,
            'elapsed_s':    round(elapsed, 1),
        }
