Run SwingTradeIQ weekly review — typically Friday after 3:30 PM IST or over the weekend.
Full pipeline re-run + performance analysis of the trading week.

## Step 1 — Execute

```bash
source venv/bin/activate
python swingtrade_iq.py --mode review
```

`review` is an alias for `scan` — it re-runs all 10 agents with fresh data.

## Step 2 — Read output files

Primary:
- `outputs/report_YYYY-MM-DD.json` — today's master report
- `data/portfolio/portfolio_state.json` — current portfolio after pipeline
- `data/portfolio/scenario_analysis.json` — forward-looking scenarios

For week-over-week context, also check:
- `logs/` directory — look for the most recent prior scan log (earlier this week) to compare deployed capital and P&L

Read `data/risk/risk_summary.json` and `data/signals/signals_summary.json`
for the full-universe view (not just selected positions).

## Step 3 — Present the weekly review report

Format in this exact structure:

---

### SWINGTRADE IQ — WEEKLY REVIEW | Week ending [DATE]

> Capital: ₹[CAPITAL] | Reviewed: [N] stocks | Positions: [N] open | Pipeline time: [N]s

---

#### 1. WEEK IN REVIEW

Write a 3–4 sentence market narrative for the week:
- Was the market bullish, bearish, or ranging? (infer from the technical scores — if most tickers show STRONG_SELL or SELL, market likely fell)
- Which sectors were strongest / weakest? (infer from fundamental + technical mix)
- Did the pipeline generate actionable setups or did it mostly produce NO_TRADE?
- What was the macro backdrop? (mention if any known events like RBI policy, earnings season, global cues — use references/risk-calendar.md if available)

---

#### 2. SIGNAL SCORECARD

For every ticker in the universe:
```
Ticker      Sector              Fund   Tech   Signal       Decision      Change from last week
──────────────────────────────────────────────────────────────────────────────────────────────
INFY        Technology          X.X    X.X    SWING_BUY    APPROVED      ↑ was WATCH
HDFCBANK    Financial Services  X.X    X.X    WATCH        APPROVED_RED  → unchanged
TCS         Technology          X.X    X.X    NO_TRADE     REJECTED      ↓ was WATCH
BAJAJ-AUTO  Consumer Cyclical   X.X    X.X    WATCH        APPROVED      → unchanged
```

"Change from last week" — if no prior state is available, write "—".
Highlight any upgrades (WATCH → SWING_BUY) with ↑ and any downgrades (WATCH → NO_TRADE) with ↓.

---

#### 3. CURRENT PORTFOLIO PERFORMANCE

For every position currently selected by the pipeline:

**[TICKER] — [SIGNAL] | [CONVICTION]**
```
Score breakdown   : Combined X.X  =  Tech X.X (×0.60)  +  Fund X.X (×0.40)
Patterns this week: [list, or "none new"]
─────────────────────────────────────────────────────────────────────
Entry (if new)    : ₹X,XXX.XX  (new setup this week)
  — OR —
Ongoing position  : Entered [DATE], now Day [N] of 20
  Unrealised P&L  : ₹±X,XXX  ([±X.X]%)
  Stop loss       : ₹X,XXX  |  Target 1: ₹X,XXX
─────────────────────────────────────────────────────────────────────
Shares / Position : [N] shares  |  ₹X,XXX  (X.X% of capital)
Capital at risk   : ₹X,XXX  (X.XX%)
Risk score        : X.X / 10  |  Decision: [APPROVED/REDUCED/REJECTED]
```

---

#### 4. PORTFOLIO METRICS — WEEK CLOSE

```
Capital deployed       : ₹XX,XXX  (XX.X%)
Cash reserve           : ₹X,XX,XXX  (XX.X%)
Total at risk          : ₹X,XXX  (X.XX% — target ≤ 6%)

Expected return (p.a.) : [±X.X]%
Portfolio volatility   : X.X% p.a.
Portfolio Sharpe       : X.XXX
Portfolio beta         : X.XXX  (market sensitivity)
Diversification ratio  : X.XXX× (correlation benefit)

Sector exposure:
  [Sector 1]  ₹XX,XXX  (XX.X%)  ✅/⚠
  [Sector 2]  ₹XX,XXX  (XX.X%)  ✅/⚠
```

---

#### 5. SCENARIO ANALYSIS — FORWARD LOOK

```
Scenario              P&L (₹)     % Capital    Notes
──────────────────────────────────────────────────────────────────────────
Bear (all SL hit)     −₹X,XXX     −X.XX%       "Manageable — within 2% rule"
Bull T1 (all hit)     +₹X,XXX     +X.XX%       "Expected outcome at 2:1 R/R"
Bull T2 (all hit)     +₹X,XXX     +X.XX%       "Trail stops after T1 for this"
Expected value        ±₹X,XXX                  "Positive = edge present"
```

For the bear scenario, add context: "This is the maximum portfolio loss if every stop triggers simultaneously — a low-probability event, but useful to know before the weekend."

---

#### 6. KELLY CALIBRATION CHECK

Run this check every week. Use data from `data/positions/positions_summary.json`:

```
Live Kelly inputs (from PositionSizerAgent this week):
  Win rate assumption : 50%  (model baseline)
  Kelly fraction      : 0.5  (half-Kelly applied)
  Avg R/R in setups   : X.X : 1  (from trade levels)
  Trade Kelly         : XX.X%  (win_rate − loss_rate / R_ratio)
  Half-Kelly applied  : XX.X%  of equity per position
```

**Calibration verdict** — choose one:
- Win rate not yet observable from live trades (< 10 trades): "Insufficient live trade history — Kelly assumptions unchanged."
- If running live trades exist in data/backtest/ or logs: compute actual win rate and compare.
  - Actual WR within ±5% of 50%: "✅ Kelly well-calibrated — no adjustment needed."
  - Actual WR 40–45%: "⚠ Win rate tracking below 50%. Consider reducing kelly_fraction from 0.5 → 0.4 in config.yaml."
  - Actual WR < 40%: "❌ Win rate significantly below model. Reduce kelly_fraction to 0.3 and review signal filters."

---

#### 7. REJECTED TICKERS — WHY

For every ticker that was REJECTED this week, give a specific reason:

```
[TICKER] — REJECTED
  Signal      : [NO_TRADE / WATCH]
  Risk verdict: [REJECTED reason]
  Core issue  : [e.g., "R/R = 1.2 < 1.5 minimum", "Sharpe negative",
                  "ATR% = 9.1% > 8% hard limit", "Max DD = −52%"]
  Watch level : "Re-enter watchlist if [condition] — e.g., price crosses SMA50
                 from below with RSI > 35 and MACD bull cross"
```

---

#### 8. NEXT WEEK PREPARATION

**Setups to watch:**
For each SWING_BUY or high-conviction WATCH signal, give the specific trigger:
```
[TICKER] — [SIGNAL]
  Entry trigger : Price ≥ ₹X,XXX with volume > 20-day avg
  Limit price   : ₹X,XXX.XX
  Stop loss     : ₹X,XXX.XX  (set GTT immediately on entry)
  Target 1      : ₹X,XXX.XX  (set GTT limit sell at this level)
  Max position  : [N] shares  (₹X,XXX — X.X% of capital)
  Key condition : "Only enter if MACD remains bullish at Monday open"
```

**Capital available for new positions:**
```
Current cash    : ₹X,XX,XXX
Max deployable  : ₹XX,XXX  (20% cap per position)
Slots available : [N]  (max [MAX_CONCURRENT] positions, [N] currently open)
Budget for week : ₹XX,XXX  (can take [N] new positions at full size)
```

**Risk calendar for next week:**
Check `references/risk-calendar.md` if it exists. Otherwise note any tickers with:
- Earnings announcements (⚠ consider not entering or exiting before)
- Ex-dividend dates (⚠ price typically drops by dividend amount)
- RBI / SEBI announcements
If none found: "No major events identified for next week — proceed with planned setups."

---

#### 9. SYSTEM HEALTH CHECK

Quick status of the SwingTradeIQ pipeline itself:

```
Last data fetch    : [date from logs/fetch_*.txt]
Data quality       : [N] tickers passed validation, [N] failed
Oldest OHLCV       : [earliest date in data/raw/]
Signal consistency : [N] of [N] tickers produced valid signals
Log errors         : [any ERRORs in logs/ this week, or "None"]
```

If data is stale (last fetch > 2 days ago): "⚠ Data is [N] days old — re-run /scan to refresh."
If any agent produced errors in logs: show the error snippet and suggest a fix.

---

#### 10. WEEK-OVER-WEEK SUMMARY (one paragraph)

Write a plain-English wrap-up paragraph that covers:
- How many new setups were identified this week vs last
- Whether the portfolio is growing, flat, or drawing down
- The single most important thing to watch next week (top conviction ticker or key risk)
- One sentence on risk: "The portfolio is [X.XX]% at risk this week — [within/above] the 2% per-trade rule"

---

### DISCLAIMER

> ⚠️ SwingTradeIQ is for educational purposes only as part of IBS India MBA — Advanced Business Analytics. Weekly review outputs are generated by an automated algorithm using publicly available data. Signal scores, Kelly calculations, and trade recommendations reflect model assumptions — not guaranteed outcomes. Past week's performance does not predict next week's results. Not SEBI-registered investment advice. Verify all prices and corporate actions independently before placing orders. Consult a SEBI-registered investment advisor for personalised guidance.
