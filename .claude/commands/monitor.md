Run SwingTradeIQ in monitor mode — intraday or pre-market position health check.
Use Tuesday–Thursday when positions are open. Does NOT re-fetch OHLCV data.

## Step 1 — Execute

```bash
source venv/bin/activate
python swingtrade_iq.py --mode monitor
```

This re-runs agents 5–10 (Technical → IndicatorEngine → Risk → PositionSizer →
PortfolioManager → ReportAgent) on existing data. Takes < 5 seconds.

## Step 2 — Read output files

- `data/portfolio/portfolio_state.json` — positions, entry prices, stops, targets
- `data/portfolio/trade_orders.json` — original order details
- `data/portfolio/scenario_analysis.json` — per-position P&L scenarios
- `data/portfolio/portfolio_metrics.json` — portfolio-level risk metrics
- `data/technical/[TICKER]_technical.json` — latest technical snapshot for each position

For each open position, also read `data/risk/[TICKER]_risk.json` to get the latest VaR and risk score.

## Step 3 — Present the monitor report

Format in this exact structure:

---

### SWINGTRADE IQ — POSITION MONITOR | [DATE] | [TIME] IST

> [N] open positions | ₹[DEPLOYED] deployed | ₹[CASH] cash available

---

#### 1. ALERTS  ⚠

Check each position against these thresholds and list alerts FIRST before anything else:

**STOP LOSS ALERTS** (price within 2% of stop):
For any position where `(current_price − stop_loss) / current_price < 0.02`:
```
🔴 [TICKER] — STOP PROXIMITY ALERT
   Entry: ₹X,XXX  |  Stop: ₹X,XXX  |  Current (last close): ₹X,XXX
   Distance to stop: X.X%  ← WITHIN 2% DANGER ZONE
   Action: Review position. Consider exiting early to preserve capital
           if today's price action shows weakness (closes below SMA20).
```

**TARGET ALERTS** (price within 3% of Target 1):
For any position where `(target_1 − current_price) / current_price < 0.03`:
```
🟢 [TICKER] — TARGET 1 PROXIMITY ALERT
   Entry: ₹X,XXX  |  T1: ₹X,XXX  |  Current: ₹X,XXX
   Distance to T1: X.X%  ← WITHIN 3% — potential exit tomorrow
   Action: Place limit sell at ₹[T1] tonight via GTT order.
           After T1 hit, move stop to breakeven (entry price).
```

**TIMEOUT ALERTS** (position held ≥ 15 trading days):
```
⏰ [TICKER] — APPROACHING MAX HOLD (Day [N] of 20)
   Still [N] days before forced TIMEOUT exit.
   Current P&L: ₹±X,XXX ([±X.X]%)
   Action: If no clear momentum toward T1, consider voluntary exit now
           to free capital for fresher setups.
```

If no alerts: "✅ No alerts — all positions within normal range."

---

#### 2. POSITION-BY-POSITION STATUS

For each open position, print a complete status block:

**[TICKER] | [SIGNAL] | Day [N] of 20**
```
Entry date       : [DATE]
Entry price      : ₹X,XXX.XX
Last close       : ₹X,XXX.XX  (as of last data)
─────────────────────────────────────────────
Unrealised P&L   : ₹±X,XXX  ([±X.X]%)   [🟢 profit / 🔴 loss]
R-multiple live  : [±X.XX]R  (target = +2.0R at T1)
─────────────────────────────────────────────
Stop loss        : ₹X,XXX.XX  ([−X.X]% from last close)
Target 1         : ₹X,XXX.XX  ([+X.X]% from last close)  ← primary exit
Target 2         : ₹X,XXX.XX  ([+X.X]% from last close)  ← trail after T1
─────────────────────────────────────────────
Shares held      : [N]  (₹X,XXX position value)
Capital at risk  : ₹X,XXX  ([X.XX]% of total capital)
Risk score       : X.X / 10
Sector / Beta    : [SECTOR]  |  β = X.XXX
─────────────────────────────────────────────
Technical now    : RSI=[XX.X]  SMA20=[₹X,XXX]  SMA50=[₹X,XXX]
                   Price vs SMA20: [above/below by X.X%]
                   MACD: [bullish/bearish/neutral]
─────────────────────────────────────────────
Position health  : [HEALTHY / AT RISK / APPROACHING STOP / TARGET IN SIGHT]
```

Calculate unrealised P&L as: `(last_close − entry_price) × shares`.
Calculate live R-multiple as: `unrealised_pnl / (shares × (entry_price − stop_loss))`.
Derive "Position health" from the following rules:
- HEALTHY: price > entry, distance to stop > 5%, live R > 0
- AT RISK: price < entry but above stop, OR live R < −0.5
- APPROACHING STOP: distance to stop < 2% (triggers Alert section too)
- TARGET IN SIGHT: distance to T1 < 3%

---

#### 3. PORTFOLIO HEALTH SUMMARY

```
Total open positions : [N] of [MAX] allowed
Capital deployed     : ₹XX,XXX  (XX.X%)
Cash available       : ₹X,XX,XXX  (XX.X%)
Total unrealised P&L : ₹±X,XXX  ([±X.X]%)   [🟢 / 🔴]
Total at risk        : ₹X,XXX  (X.XX% of capital)
Portfolio beta       : X.XXX  (market sensitivity)

Bear scenario (all SL hit)  : ₹−X,XXX  (−X.XX%)
Bull T1 scenario (all hit)  : ₹+X,XXX  (+X.XX%)
Expected value              : ₹±X,XXX
```

Sector exposure check — flag any sector > 30%:
```
[Sector]     ₹XX,XXX  (XX.X%)   ✅ / ⚠ APPROACHING LIMIT
```

---

#### 4. STOP MANAGEMENT GUIDANCE

For each position that is in profit (unrealised P&L > 0), evaluate whether to trail the stop:

**Trail stop rules:**
- If live P&L > +0.5R: consider moving stop to breakeven (entry price)
- If live P&L > +1.0R: consider moving stop to +0.5R (lock in half a reward unit)
- If live P&L > +1.5R and approaching T1: set GTT at T1, let it execute

Print specific guidance:
```
[TICKER]: Stop currently at ₹X,XXX. Unrealised = +X.XXR.
          → Suggest trailing stop to ₹X,XXX (breakeven + buffer)
          → This locks in ₹0 minimum outcome on this trade.
```

If a position is at a loss but above the stop, do not trail — let the original stop hold.

---

#### 5. TOMORROW'S ACTION CHECKLIST

Numbered, specific actions for the next trading session:

1. **[TIME] 9:00 AM IST — Pre-market check**
   Verify [TICKER] opens above stop ₹X,XXX. If opens below → exit immediately at market.

2. **[TIME] During market — GTT orders to verify active**
   - [TICKER]: GTT stop at ₹X,XXX  |  GTT target at ₹X,XXX — confirm active in broker
   - [TICKER]: GTT stop at ₹X,XXX  |  GTT target at ₹X,XXX — confirm active in broker

3. **Exit candidates** (if any position is AT RISK or APPROACHING STOP):
   "[TICKER] — consider voluntary exit at open if it continues showing weakness.
   Saves ₹X vs waiting for stop hit (gap-down risk)."

4. **New entry watch** (WATCH-signal tickers from latest scan):
   "[TICKER] — set price alert at ₹X,XXX. If it triggers and MACD is still bullish,
   enter with [N] shares at market/limit."

5. **Run next scan**: "Re-run /scan after 3:30 PM IST tomorrow for updated signals."

---

### DISCLAIMER

> ⚠️ SwingTradeIQ is for educational purposes only as part of IBS India MBA — Advanced Business Analytics. Position P&L shown is based on last available close price and does not reflect real-time market data. Stop losses and targets are indicative — actual execution prices may differ due to gaps, liquidity, or circuit breakers. Not SEBI-registered investment advice. Consult a registered investment advisor before acting on any output.
