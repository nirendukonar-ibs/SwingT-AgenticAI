# SwingTradeIQ — Agent 10/10: ReportAgent
# Built in Session 10.
# Handoff: reads all data/ directories → writes outputs/

import time, json, csv
from pathlib import Path
from datetime import datetime

import yaml


# ─── Colour palette ──────────────────────────────────────────────────────────
_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', Arial, sans-serif; background: #0f1117;
       color: #e2e8f0; line-height: 1.55; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 32px 24px; }
h1 { font-size: 1.9rem; font-weight: 700; color: #f0f4ff; }
h2 { font-size: 1.1rem; font-weight: 600; color: #94a3b8; text-transform: uppercase;
     letter-spacing: .08em; margin: 36px 0 12px; border-bottom: 1px solid #1e293b;
     padding-bottom: 6px; }
.meta { color: #64748b; font-size: .85rem; margin-top: 4px; }
.kpi-row { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 8px; }
.kpi  { background: #1e293b; border-radius: 8px; padding: 16px 20px;
        flex: 1; min-width: 150px; }
.kpi .label { font-size: .75rem; color: #64748b; text-transform: uppercase;
              letter-spacing: .06em; }
.kpi .value { font-size: 1.5rem; font-weight: 700; margin-top: 4px; }
.green { color: #4ade80; } .red { color: #f87171; } .amber { color: #fbbf24; }
.blue  { color: #60a5fa; } .muted { color: #94a3b8; }
table { width: 100%; border-collapse: collapse; font-size: .88rem; margin-top: 8px; }
th { background: #1e293b; color: #94a3b8; text-align: left; padding: 9px 12px;
     font-weight: 600; font-size: .78rem; text-transform: uppercase; letter-spacing:.04em; }
td { padding: 9px 12px; border-bottom: 1px solid #1e293b; }
tr:hover td { background: #1a2235; }
.badge { display:inline-block; padding: 2px 8px; border-radius: 4px;
         font-size:.75rem; font-weight:600; }
.b-buy  { background:#16313b; color:#4ade80; }
.b-sell { background:#2d1b1b; color:#f87171; }
.b-watch{ background:#2d2510; color:#fbbf24; }
.b-none { background:#1e293b; color:#64748b; }
.footer { color: #334155; font-size: .78rem; margin-top: 48px; text-align: center; }
.section-card { background:#151e2d; border-radius:10px; padding:20px 24px; margin-bottom:16px; }
"""

_SIGNAL_BADGE = {
    'SWING_BUY': '<span class="badge b-buy">SWING BUY</span>',
    'WATCH':     '<span class="badge b-watch">WATCH</span>',
    'NO_TRADE':  '<span class="badge b-none">NO TRADE</span>',
    'BUY':       '<span class="badge b-buy">BUY</span>',
    'STRONG_BUY':'<span class="badge b-buy">STRONG BUY</span>',
    'SELL':      '<span class="badge b-sell">SELL</span>',
    'STRONG_SELL':'<span class="badge b-sell">STRONG SELL</span>',
    'NEUTRAL':   '<span class="badge b-none">NEUTRAL</span>',
}


def _badge(sig: str) -> str:
    return _SIGNAL_BADGE.get(sig, f'<span class="badge b-none">{sig}</span>')


def _colour(val, pos_good: bool = True) -> str:
    if val is None: return 'muted'
    try:
        v = float(val)
        if pos_good: return 'green' if v > 0 else ('red' if v < 0 else 'muted')
        return 'red' if v > 0 else ('green' if v < 0 else 'muted')
    except Exception:
        return 'muted'


def _fmt(val, prefix='', suffix='', d=2):
    if val is None: return '—'
    try:    return f"{prefix}{float(val):,.{d}f}{suffix}"
    except: return str(val)


class ReportAgent:
    """
    Agent 10/10 — SwingTradeIQ Report Agent.

    Aggregates outputs from all upstream agents and produces:
      • outputs/report_{DATE}.html   — full dark-themed HTML report
      • outputs/report_{DATE}.json   — machine-readable summary
      • outputs/orders_{DATE}.csv    — trade orders spreadsheet
      • logs/report_{DATE}.txt       — run log

    HTML report sections
    --------------------
    1. Header + KPI strip (capital, deployed, risk, Sharpe)
    2. Trade orders — priority-ordered execution table
    3. Portfolio metrics — return / vol / Sharpe / beta / diversification
    4. Scenario analysis — bear / bull T1 / T2 / expected value
    5. Universe overview — all tickers: fundamental + technical + signal
    6. Per-ticker deep-dive — scores, patterns, trade levels, risk flags
    7. Capital & sector allocation
    8. Footer (run metadata)
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir  = Path(base_dir)
        self.port_dir  = self.base_dir / 'data' / 'portfolio'
        self.pos_dir   = self.base_dir / 'data' / 'positions'
        self.risk_dir  = self.base_dir / 'data' / 'risk'
        self.sig_dir   = self.base_dir / 'data' / 'signals'
        self.fund_dir  = self.base_dir / 'data' / 'fundamental'
        self.tech_dir  = self.base_dir / 'data' / 'technical'
        self.eda_dir   = self.base_dir / 'data' / 'eda'
        self.meta_path = self.base_dir / 'data' / 'meta' / 'universe_meta.json'
        self.out_dir   = self.base_dir / 'outputs'
        self.log_dir   = self.base_dir / 'logs'
        self.cfg_path  = self.base_dir / 'config.yaml'

        for d in [self.out_dir, self.log_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._log_lines: list[str] = []

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self._log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _j(self, path: Path) -> dict:
        return json.load(open(path)) if path.exists() else {}

    def _all_tickers(self) -> list[str]:
        return sorted(
            f.stem.replace('_signal', '')
            for f in self.sig_dir.glob('*_signal.json')
        )

    # ──────────────────────────────────────────────────────────────────────────
    # HTML sections
    # ──────────────────────────────────────────────────────────────────────────

    def _html_header(self, cfg: dict, run_at: str, capital: float,
                     ct: dict, metrics: dict) -> str:
        sharpe = metrics.get('portfolio_sharpe')
        sc     = self._j(self.port_dir / 'scenario_analysis.json')
        ev     = (sc.get('portfolio') or {}).get('total_expected_value', 0)

        sc_clr = _colour(sharpe)
        ev_clr = _colour(ev)
        dep_clr= 'blue'

        return f"""
<div class="wrap">
<h1>SwingTradeIQ — Scan Report</h1>
<p class="meta">Run: {run_at} &nbsp;|&nbsp; Capital: ₹{capital:,.0f}
   &nbsp;|&nbsp; Universe: {len(self._all_tickers())} stocks</p>

<h2>Portfolio Snapshot</h2>
<div class="kpi-row">
  <div class="kpi"><div class="label">Capital Deployed</div>
    <div class="value {dep_clr}">₹{ct.get('total_deployed',0):,.0f}
    <span style="font-size:.85rem;font-weight:400"> ({ct.get('deployed_pct',0):.1f}%)</span></div></div>
  <div class="kpi"><div class="label">Cash Reserve</div>
    <div class="value green">₹{ct.get('remaining_cash',0):,.0f}</div></div>
  <div class="kpi"><div class="label">Total at Risk</div>
    <div class="value amber">₹{ct.get('total_risk',0):,.0f}
    <span style="font-size:.85rem;font-weight:400"> ({ct.get('risk_pct',0):.2f}%)</span></div></div>
  <div class="kpi"><div class="label">Portfolio Sharpe</div>
    <div class="value {sc_clr}">{_fmt(sharpe, d=3)}</div></div>
  <div class="kpi"><div class="label">Expected Value</div>
    <div class="value {ev_clr}">₹{_fmt(ev, d=0)}</div></div>
</div>"""

    def _html_orders(self, orders: list[dict]) -> str:
        if not orders:
            return '<p class="muted">No actionable orders.</p>'
        rows = ''
        for o in orders:
            rows += f"""<tr>
  <td><b>{o['priority']}</b></td>
  <td><b>{o['ticker_nse']}</b></td>
  <td>{_badge(o['signal'])}</td>
  <td>{o.get('conviction','—')}</td>
  <td>{o['quantity']}</td>
  <td>₹{_fmt(o['limit_price'])}</td>
  <td class="red">₹{_fmt(o['stop_loss'])}</td>
  <td class="green">₹{_fmt(o['target_1'])}</td>
  <td class="green">₹{_fmt(o['target_2'])}</td>
  <td>{o['risk_reward']}:1</td>
  <td>₹{_fmt(o['position_value'])}</td>
  <td class="red">₹{_fmt(o['capital_at_risk'])}</td>
</tr>"""
        return f"""
<h2>Trade Orders</h2>
<div class="section-card">
<table>
<tr><th>#</th><th>Ticker</th><th>Signal</th><th>Conviction</th><th>Qty</th>
    <th>Limit ₹</th><th>Stop ₹</th><th>T1 ₹</th><th>T2 ₹</th>
    <th>R/R</th><th>Position ₹</th><th>At Risk ₹</th></tr>
{rows}</table></div>"""

    def _html_metrics(self, metrics: dict) -> str:
        def row(label, val, suffix='', pos_good=True):
            clr = _colour(val, pos_good)
            return f'<tr><td>{label}</td><td class="{clr}"><b>{_fmt(val, suffix=suffix)}</b></td></tr>'
        return f"""
<h2>Portfolio Analytics</h2>
<div class="section-card">
<table style="max-width:520px">
{row('Expected Return (p.a.)', metrics.get('portfolio_expected_return_pct'), '%')}
{row('Portfolio Volatility (p.a.)', metrics.get('portfolio_volatility_pct'), '%', False)}
{row('Portfolio Sharpe Ratio', metrics.get('portfolio_sharpe'))}
{row('Portfolio Beta', metrics.get('portfolio_beta'))}
{row('Diversification Ratio', metrics.get('diversification_ratio'))}
<tr><td>Correlation-adjusted</td>
    <td class="muted">{'Yes' if metrics.get('note_correlation_used') else 'No'}</td></tr>
</table></div>"""

    def _html_scenarios(self, sc: dict) -> str:
        pf  = sc.get('portfolio', {})
        rows_pos = ''
        for r in sc.get('per_position', []):
            ev_clr = _colour(r['expected_value'])
            rows_pos += f"""<tr>
  <td><b>{r['ticker']}</b></td>
  <td>{r['shares']}</td>
  <td class="red">₹{_fmt(r['bear_pnl'], d=0)}</td>
  <td class="green">₹{_fmt(r['bull_t1_pnl'], d=0)}</td>
  <td class="green">₹{_fmt(r['bull_t2_pnl'], d=0)}</td>
  <td class="{ev_clr}">₹{_fmt(r['expected_value'], d=0)}</td>
  <td>{_fmt(r['win_rate_used']*100, suffix='%', d=1)}</td>
</tr>"""
        total_ev_clr = _colour(pf.get('total_expected_value'))
        return f"""
<h2>Scenario Analysis</h2>
<div class="section-card">
<div class="kpi-row">
  <div class="kpi"><div class="label">Bear (all SL hit)</div>
    <div class="value red">₹{_fmt(pf.get('bear_total_pnl'), d=0)}
    <span style="font-size:.85rem"> ({_fmt(pf.get('bear_pnl_pct'))}%)</span></div></div>
  <div class="kpi"><div class="label">Bull T1 (all hit)</div>
    <div class="value green">₹{_fmt(pf.get('bull_t1_total_pnl'), d=0)}
    <span style="font-size:.85rem"> ({_fmt(pf.get('bull_t1_pnl_pct'))}%)</span></div></div>
  <div class="kpi"><div class="label">Bull T2 (all hit)</div>
    <div class="value green">₹{_fmt(pf.get('bull_t2_total_pnl'), d=0)}
    <span style="font-size:.85rem"> ({_fmt(pf.get('bull_t2_pnl_pct'))}%)</span></div></div>
  <div class="kpi"><div class="label">Expected Value</div>
    <div class="value {total_ev_clr}">₹{_fmt(pf.get('total_expected_value'), d=0)}</div></div>
</div>
<table>
<tr><th>Ticker</th><th>Shares</th><th>Bear ₹</th><th>T1 ₹</th>
    <th>T2 ₹</th><th>Exp Value ₹</th><th>Win %</th></tr>
{rows_pos}</table></div>"""

    def _html_universe(self, tickers: list[str]) -> str:
        rows = ''
        for t in tickers:
            sig  = self._j(self.sig_dir  / f'{t}_signal.json')
            fund = self._j(self.fund_dir / f'{t}_fundamental.json')
            tech = self._j(self.tech_dir / f'{t}_technical.json')
            risk = self._j(self.risk_dir / f'{t}_risk.json')
            fs = fund.get('fundamental_score', '—')
            ts = tech.get('technical_score', '—')
            cs = sig.get('combined_score', '—')
            dec= risk.get('risk_decision', '—')
            dec_clr = {'APPROVED':'green','APPROVED_REDUCED':'amber','REJECTED':'red'}.get(dec,'muted')
            rs = risk.get('risk_score', '—')
            rows += f"""<tr>
  <td><b>{t}</b></td>
  <td>{fund.get('sector','—')}</td>
  <td>{_badge(sig.get('signal','—'))}</td>
  <td>{sig.get('conviction','—')}</td>
  <td class="{_colour(cs)}">{_fmt(cs)}/10</td>
  <td class="{_colour(fs)}">{_fmt(fs)}/10</td>
  <td>{_badge(tech.get('signal','—'))}</td>
  <td class="{_colour(ts)}">{_fmt(ts)}/10</td>
  <td class="{dec_clr}"><b>{dec}</b></td>
  <td class="{'red' if isinstance(rs,(int,float)) and rs>6 else 'green'}">{_fmt(rs)}</td>
</tr>"""
        return f"""
<h2>Universe Overview</h2>
<div class="section-card">
<table>
<tr><th>Ticker</th><th>Sector</th><th>Signal</th><th>Conviction</th>
    <th>Combined</th><th>Fund</th><th>Tech Signal</th><th>Tech Score</th>
    <th>Risk Decision</th><th>Risk Score</th></tr>
{rows}</table></div>"""

    def _html_deep_dive(self, tickers: list[str], selected: list[str]) -> str:
        html = '<h2>Per-Ticker Detail</h2>'
        for t in tickers:
            sig  = self._j(self.sig_dir  / f'{t}_signal.json')
            fund = self._j(self.fund_dir / f'{t}_fundamental.json')
            tech = self._j(self.tech_dir / f'{t}_technical.json')
            risk = self._j(self.risk_dir / f'{t}_risk.json')
            pos  = self._j(self.pos_dir  / f'{t}_position.json')
            eda  = self._j(self.eda_dir  / f'{t}_eda.json')

            fin  = pos.get('final') or {}
            rs   = eda.get('return_stats') or {}
            dd   = eda.get('drawdown') or {}
            sn   = tech.get('snapshot') or {}
            lv   = sig.get('trade_levels') or {}
            rf   = fund.get('raw_fundamentals') or {}
            flags= risk.get('risk_flags') or []
            vm   = risk.get('var_metrics') or {}

            in_port = '✅ IN PORTFOLIO' if t in selected else ''
            pat = ', '.join(sig.get('patterns',[]) or []) or '—'

            fund_rows = ''.join(
                f"<tr><td>{k}</td><td>{_fmt(v)}</td></tr>"
                for k, v in rf.items() if v is not None
            )
            comp_rows = ''
            for comp, cv in (tech.get('components') or {}).items():
                bar = '█' * int(cv['score'])
                comp_rows += f"<tr><td>{comp}</td><td>{_fmt(cv['score'])}/10</td><td>{bar}</td></tr>"

            html += f"""
<div class="section-card">
<h2 style="margin-top:0">{t} &nbsp;
  {_badge(sig.get('signal','—'))} &nbsp;
  <span style="font-size:.85rem;color:#4ade80">{in_port}</span></h2>
<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px">

<div>
<b style="color:#94a3b8">Scores</b>
<table>
<tr><td>Combined</td><td class="{_colour(sig.get('combined_score'))}">{_fmt(sig.get('combined_score'))}/10</td></tr>
<tr><td>Fundamental</td><td class="{_colour(fund.get('fundamental_score'))}">{_fmt(fund.get('fundamental_score'))}/10</td></tr>
<tr><td>Technical</td><td class="{_colour(tech.get('technical_score'))}">{_fmt(tech.get('technical_score'))}/10</td></tr>
<tr><td>Risk score</td><td class="amber">{_fmt(risk.get('risk_score'))}/10</td></tr>
</table>
<br/><b style="color:#94a3b8">EDA</b>
<table>
<tr><td>Ann. return</td><td class="{_colour(rs.get('annualised_return_pct'))}">{_fmt(rs.get('annualised_return_pct'))}%</td></tr>
<tr><td>Ann. vol</td><td>{_fmt(rs.get('annualised_volatility_pct'))}%</td></tr>
<tr><td>Sharpe</td><td class="{_colour(rs.get('sharpe_ratio'))}">{_fmt(rs.get('sharpe_ratio'),d=3)}</td></tr>
<tr><td>Max drawdown</td><td class="red">{_fmt(dd.get('max_drawdown_pct'))}%</td></tr>
<tr><td>Skewness</td><td>{_fmt(rs.get('skewness'),d=3)}</td></tr>
<tr><td>Kurtosis</td><td>{_fmt(rs.get('kurtosis'),d=3)}</td></tr>
</table>
</div>

<div>
<b style="color:#94a3b8">Technicals</b>
<table>
<tr><td>Price</td><td>₹{_fmt(sn.get('price'))}</td></tr>
<tr><td>SMA20</td><td>₹{_fmt(sn.get('sma20'))}</td></tr>
<tr><td>SMA50</td><td>₹{_fmt(sn.get('sma50'))}</td></tr>
<tr><td>SMA200</td><td>₹{_fmt(sn.get('sma200'))}</td></tr>
<tr><td>RSI 14</td><td>{_fmt(sn.get('rsi14'))}</td></tr>
<tr><td>Stoch %K</td><td>{_fmt(sn.get('stoch_k'))}</td></tr>
<tr><td>ATR%</td><td>{_fmt(sn.get('atr_pct'))}%</td></tr>
<tr><td>52w high</td><td class="red">{_fmt(tech.get('52w',{}).get('pct_from_high'))}%</td></tr>
</table>
<br/><b style="color:#94a3b8">Patterns</b>
<p style="color:#fbbf24;font-size:.88rem;margin-top:4px">{pat}</p>
</div>

<div>
<b style="color:#94a3b8">Fundamentals</b>
<table>{fund_rows}</table>
</div>
</div>

<div style="margin-top:16px;display:grid;grid-template-columns:1fr 1fr;gap:16px">
<div>
<b style="color:#94a3b8">Technical component scores</b>
<table style="margin-top:4px">{comp_rows}</table>
</div>
<div>
<b style="color:#94a3b8">Trade & risk detail</b>
<table style="margin-top:4px">
{''.join([f"<tr><td>Entry</td><td>₹{_fmt(lv.get('entry'))}</td></tr>",
  f"<tr><td>Stop loss</td><td class='red'>₹{_fmt(lv.get('stop_loss'))}</td></tr>",
  f"<tr><td>Target 1</td><td class='green'>₹{_fmt(lv.get('target_1'))}</td></tr>",
  f"<tr><td>Target 2</td><td class='green'>₹{_fmt(lv.get('target_2'))}</td></tr>",
  f"<tr><td>R/R</td><td>{_fmt(lv.get('risk_reward_1'))}:1</td></tr>",
  f"<tr><td>Shares</td><td>{fin.get('shares','—')}</td></tr>",
  f"<tr><td>Position value</td><td>₹{_fmt(fin.get('position_value_inr'),d=0)}</td></tr>",
  f"<tr><td>At risk</td><td class='red'>₹{_fmt(fin.get('capital_at_risk_inr'),d=0)}</td></tr>",
  f"<tr><td>VaR 95%</td><td class='red'>{_fmt(vm.get('daily_var_95_pct'))}%</td></tr>",
  f"<tr><td>CVaR 95%</td><td class='red'>{_fmt(vm.get('daily_cvar_95_pct'))}%</td></tr>",
  f"<tr><td>Risk flags</td><td class='amber'>{', '.join(flags) or '—'}</td></tr>",
  f"<tr><td>Risk decision</td><td>{risk.get('risk_decision','—')}</td></tr>",
]) if lv else '<tr><td colspan=2 class="muted">No trade levels</td></tr>'
}
</table>
</div>
</div>
</div>"""
        return html

    def _html_allocation(self, ct: dict) -> str:
        sec_rows = ''
        for s, sv in ct.get('sector_allocation', {}).items():
            breach = ' <span class="red">⚠ BREACH</span>' if sv.get('breach') else ''
            sec_rows += f"<tr><td>{s}</td><td>₹{_fmt(sv['value'],d=0)}</td><td>{_fmt(sv['pct'])}%{breach}</td></tr>"
        return f"""
<h2>Capital &amp; Sector Allocation</h2>
<div class="section-card" style="display:grid;grid-template-columns:1fr 1fr;gap:24px">
<div>
<b style="color:#94a3b8">Capital Summary</b>
<table style="margin-top:8px">
<tr><td>Total capital</td><td class="blue">₹{_fmt(ct.get('capital'),d=0)}</td></tr>
<tr><td>Deployed</td><td class="blue">₹{_fmt(ct.get('total_deployed'),d=0)} ({_fmt(ct.get('deployed_pct'))}%)</td></tr>
<tr><td>Cash reserve</td><td class="green">₹{_fmt(ct.get('remaining_cash'),d=0)} ({_fmt(ct.get('cash_pct'))}%)</td></tr>
<tr><td>Total at risk</td><td class="red">₹{_fmt(ct.get('total_risk'),d=0)} ({_fmt(ct.get('risk_pct'))}%)</td></tr>
</table>
</div>
<div>
<b style="color:#94a3b8">Sector Exposure</b>
<table style="margin-top:8px">
<tr><th>Sector</th><th>Value ₹</th><th>% Capital</th></tr>
{sec_rows}
</table>
</div>
</div>"""

    def _build_html(self, run_at: str) -> str:
        cfg     = yaml.safe_load(open(self.cfg_path))
        capital = cfg['portfolio']['total_capital']
        state   = self._j(self.port_dir / 'portfolio_state.json')
        orders  = self._j(self.port_dir / 'trade_orders.json').get('orders', [])
        metrics = self._j(self.port_dir / 'portfolio_metrics.json')
        sc      = self._j(self.port_dir / 'scenario_analysis.json')
        ct      = state.get('capital_table', {})
        sel     = [p['ticker'] for p in state.get('positions', [])]
        tickers = self._all_tickers()

        body = (
            self._html_header(cfg, run_at, capital, ct, metrics)
            + self._html_orders(orders)
            + self._html_metrics(metrics)
            + self._html_scenarios(sc)
            + self._html_universe(tickers)
            + self._html_deep_dive(tickers, sel)
            + self._html_allocation(ct)
            + f'<div class="footer">SwingTradeIQ &nbsp;|&nbsp; Generated {run_at} &nbsp;|&nbsp;'
              f' IBS India MBA — Advanced Business Analytics</div>'
            + '</div>'
        )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SwingTradeIQ Report — {run_at[:10]}</title>
<style>{_CSS}</style>
</head>
<body>
{body}
</body>
</html>"""

    # ──────────────────────────────────────────────────────────────────────────
    # JSON summary
    # ──────────────────────────────────────────────────────────────────────────

    def _build_json(self, run_at: str) -> dict:
        state   = self._j(self.port_dir / 'portfolio_state.json')
        metrics = self._j(self.port_dir / 'portfolio_metrics.json')
        sc      = self._j(self.port_dir / 'scenario_analysis.json')
        orders  = self._j(self.port_dir / 'trade_orders.json').get('orders', [])
        tickers = self._all_tickers()

        universe = []
        for t in tickers:
            sig  = self._j(self.sig_dir  / f'{t}_signal.json')
            fund = self._j(self.fund_dir / f'{t}_fundamental.json')
            tech = self._j(self.tech_dir / f'{t}_technical.json')
            risk = self._j(self.risk_dir / f'{t}_risk.json')
            pos  = self._j(self.pos_dir  / f'{t}_position.json')
            universe.append({
                'ticker':          t,
                'sector':          fund.get('sector'),
                'signal':          sig.get('signal'),
                'conviction':      sig.get('conviction'),
                'combined_score':  sig.get('combined_score'),
                'fundamental_score':fund.get('fundamental_score'),
                'technical_score': tech.get('technical_score'),
                'risk_decision':   risk.get('risk_decision'),
                'risk_score':      risk.get('risk_score'),
                'final_shares':    (pos.get('final') or {}).get('shares', 0),
                'position_value':  (pos.get('final') or {}).get('position_value_inr', 0),
                'trade_levels':    sig.get('trade_levels'),
                'patterns':        sig.get('patterns', []),
                'risk_flags':      risk.get('risk_flags', []),
            })

        return {
            'run_at':         run_at,
            'portfolio':      state.get('capital_table'),
            'metrics':        {k: v for k, v in metrics.items() if k != 'run_at'},
            'scenarios':      sc.get('portfolio'),
            'orders':         orders,
            'universe':       universe,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # CSV orders
    # ──────────────────────────────────────────────────────────────────────────

    def _build_csv(self, orders: list[dict], path: Path) -> None:
        if not orders:
            return
        fields = ['priority','ticker_nse','exchange','action','order_type','validity',
                  'quantity','limit_price','stop_loss','target_1','target_2',
                  'risk_reward','position_value','capital_at_risk','signal',
                  'conviction','sector','rationale']
        with open(path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            w.writeheader()
            w.writerows(orders)

    # ──────────────────────────────────────────────────────────────────────────
    # Console summary
    # ──────────────────────────────────────────────────────────────────────────

    def _print_summary(self, summary: dict) -> None:
        ct = summary['portfolio']
        m  = summary['metrics']
        sc = summary['scenarios']
        orders = summary['orders']

        print(f"\n{'─'*60}")
        print(f"  CAPITAL SNAPSHOT")
        print(f"{'─'*60}")
        print(f"  Deployed  : ₹{ct['total_deployed']:>10,.0f}  ({ct['deployed_pct']:.1f}%)")
        print(f"  Cash      : ₹{ct['remaining_cash']:>10,.0f}  ({ct['cash_pct']:.1f}%)")
        print(f"  At risk   : ₹{ct['total_risk']:>10,.0f}  ({ct['risk_pct']:.2f}%)")

        print(f"\n{'─'*60}")
        print(f"  PORTFOLIO METRICS")
        print(f"{'─'*60}")
        print(f"  Exp. return : {m.get('portfolio_expected_return_pct'):+.1f}% p.a.")
        print(f"  Volatility  : {m.get('portfolio_volatility_pct'):.1f}% p.a.")
        print(f"  Sharpe      : {m.get('portfolio_sharpe')}")
        print(f"  Beta        : {m.get('portfolio_beta')}")

        print(f"\n{'─'*60}")
        print(f"  SCENARIOS")
        print(f"{'─'*60}")
        print(f"  Bear (all SL) : ₹{sc['bear_total_pnl']:>8,.0f}  ({sc['bear_pnl_pct']:.2f}%)")
        print(f"  Bull T1       : ₹{sc['bull_t1_total_pnl']:>8,.0f}  ({sc['bull_t1_pnl_pct']:.2f}%)")
        print(f"  Bull T2       : ₹{sc['bull_t2_total_pnl']:>8,.0f}  ({sc['bull_t2_pnl_pct']:.2f}%)")
        print(f"  Expected val  : ₹{sc['total_expected_value']:>8,.0f}")

        print(f"\n{'─'*60}")
        print(f"  TRADE ORDERS  ({len(orders)} orders)")
        print(f"{'─'*60}")
        print(f"  {'P':<3} {'Ticker':<16} {'Qty':>4}  {'Limit':>8}  {'SL':>8}  {'T1':>9}  Signal")
        for o in orders:
            print(f"  {o['priority']:<3} {o['ticker_nse']:<16} {o['quantity']:>4}"
                  f"  ₹{o['limit_price']:>7.2f}  ₹{o['stop_loss']:>7.2f}"
                  f"  ₹{o['target_1']:>8.2f}  {o['signal']}")

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        start_time = time.time()
        run_at     = datetime.now().strftime('%Y-%m-%d %H:%M')
        date_slug  = datetime.now().strftime('%Y-%m-%d')

        self._log(f"ReportAgent — {run_at}")
        print(f"{'='*60}")
        print(f"ReportAgent.run() — generating outputs")
        print(f"{'='*60}")

        out_paths = []

        # 1. HTML
        print("  Building HTML report ...", end=' ', flush=True)
        html = self._build_html(run_at)
        html_path = self.out_dir / f"report_{date_slug}.html"
        html_path.write_text(html, encoding='utf-8')
        out_paths.append(str(html_path))
        print(f"✅  ({html_path.stat().st_size/1024:.1f} KB)")
        self._log(f"HTML: {html_path}")

        # 2. JSON
        print("  Building JSON summary ...", end=' ', flush=True)
        summary = self._build_json(run_at)
        json_path = self.out_dir / f"report_{date_slug}.json"
        with open(json_path, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        out_paths.append(str(json_path))
        print(f"✅  ({json_path.stat().st_size/1024:.1f} KB)")
        self._log(f"JSON: {json_path}")

        # 3. CSV
        print("  Building orders CSV ...", end=' ', flush=True)
        orders    = summary['orders']
        csv_path  = self.out_dir / f"orders_{date_slug}.csv"
        self._build_csv(orders, csv_path)
        out_paths.append(str(csv_path))
        print(f"✅  ({csv_path.stat().st_size} bytes)")
        self._log(f"CSV: {csv_path}")

        # Console
        self._print_summary(summary)

        # Log
        elapsed  = time.time() - start_time
        log_path = self.log_dir / f"report_{date_slug}.txt"
        self._log(f"Done — {elapsed:.1f}s")
        with open(log_path, 'w') as f:
            f.write('\n'.join([
                'SwingTradeIQ ReportAgent',
                f"Date    : {run_at}",
                f"Duration: {elapsed:.1f}s",
                '',
                *self._log_lines,
            ]))

        print(f"\n{'='*60}")
        print(f"Done in {elapsed:.1f}s")
        for p in out_paths:
            print(f"  → {p}")
        print(f"{'='*60}")

        return {
            'succeeded':    ['report'],
            'failed':       [],
            'output_paths': out_paths,
            'elapsed_s':    round(elapsed, 1),
        }
