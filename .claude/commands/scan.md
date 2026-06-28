Run SwingTradeIQ in scan mode — full end-of-day pipeline from data fetch through report.
Run this after 3:30 PM IST on any trading day.

## Step 1 — Parse user input

Extract from the user's message:
- **Capital**: "2 lakhs" → 200000, "1.5 lakh" → 150000, "50k" → 50000. If missing, read `portfolio.total_capital` from config.yaml.
- **Universe**: check which flag to use:
  - User says specific tickers ("scan INFY, TCS, RELIANCE") → `--tickers INFY TCS RELIANCE`
  - User says index ("scan Nifty 100", "run on Nifty 500") → `--universe nifty100` or `--universe nifty500`
  - User provides a CSV ("scan using my_stocks.csv") → `--csv path/to/file.csv`
  - User says nothing → omit all universe flags (uses config.yaml `watchlist.universe`, default: nifty50)
- **Mode override**: if user says "just signals" or "no re-fetch", switch to `--mode monitor` instead.

Universe flag reference:
- Built-in (no CSV needed): `nifty50` (50), `nifty100` (100)
- Needs CSV in data/universe/: `nifty200`, `nifty500`, `nifty_midcap150`, `nifty_smallcap250`
- Run `python swingtrade_iq.py --list-universes` to see all options with download status.

## Step 2 — Pre-flight checks

Before running, verify:
```bash
test -f swingtrade_iq.py || echo "MISSING"
test -d venv || echo "NO VENV"
```
If swingtrade_iq.py is missing: "Not built yet — run through Sessions 1–10 first."
If venv is missing: "Run `python -m venv venv && pip install -r requirements.txt` first."

## Step 3 — Execute

Build the command based on what the user asked:
```bash
source venv/bin/activate
# Base command — add flags as needed:
python swingtrade_iq.py --mode scan --capital CAPITAL
# Optional universe overrides (add ONE of these, or none for nifty50 default):
#   --universe nifty100
#   --universe nifty500          # requires data/universe/nifty500.csv
#   --csv data/universe/my_picks.csv
#   --tickers INFY TCS RELIANCE
```

Stream the output. If it fails with a yfinance error, note which tickers failed and continue presenting results for the tickers that succeeded.
If universe is nifty500 and the CSV is missing, say: "Download nifty500.csv from NSEIndia → data/universe/nifty500.csv (see data/universe/README.md)"

## Step 4 — Read output files

Read these files (all are written by the pipeline):
- `outputs/report_YYYY-MM-DD.json` — master report (today's date)
- `data/portfolio/portfolio_state.json` — final positions and capital table
- `data/portfolio/trade_orders.json` — executable orders
- `data/portfolio/scenario_analysis.json` — bear/bull/EV scenarios
- `data/portfolio/portfolio_metrics.json` — Sharpe, beta, vol
- `data/signals/signals_summary.json` — all tickers' signal decisions

## Step 5 — Present the scan report

Format in this exact structure:

---

### SWINGTRADE IQ — SCAN REPORT | [DATE] | [TIME] IST

> Capital: ₹[CAPITAL] | Universe: [N] stocks | Run completed: [elapsed]s

---

#### 1. NEW TRADE SETUPS

List all tickers with signal = SWING_BUY first, then WATCH, then NO_TRADE. For each:

**[TICKER] — [SIGNAL BADGE] | Conviction: [HIGH/MEDIUM/LOW]**
```
Combined score   : X.X / 10  (Tech × 0.60 + Fund × 0.40)
Technical score  : X.X / 10  → [BUY/SELL/NEUTRAL]
Fundamental score: X.X / 10  → [PASS/FAIL]
Patterns detected: [MACD_BULL_CROSS, RSI_OVERSOLD, ...]  or  None
─────────────────────────────────────────────────────────
Entry (limit)    : ₹X,XXX.XX
Stop loss        : ₹X,XXX.XX  (−X.XX%,  2× ATR)
Target 1         : ₹X,XXX.XX  (+X.XX%,  4× ATR)  ← execute here first
Target 2         : ₹X,XXX.XX  (+X.XX%,  6× ATR)  ← trail after T1
Risk / Reward    : X.X : 1
─────────────────────────────────────────────────────────
Position size    : X shares  (₹X,XXX — X.X% of capital)
Capital at risk  : ₹X,XXX  (X.XX% of capital)
Risk decision    : [APPROVED / APPROVED_REDUCED / REJECTED]
Risk score       : X.X / 10
VaR 95%          : −X.XX%/day   CVaR 95%: −X.XX%/day
Sector           : [SECTOR]   Beta: X.XX
```

For SWING_BUY: add ✅ and "→ Place limit order at open tomorrow"
For WATCH: add ⏳ and "→ Monitor — set price alert at [entry price]"
For NO_TRADE: add ❌ and the rejection reason (e.g., "R/R < 1.5", "Sharpe negative", "NO_TRADE signal")

If no SWING_BUY signals: "No SWING_BUY setups today — market likely ranging or in downtrend. WATCH candidates listed above; wait for clearer signals."

---

#### 2. PORTFOLIO STATUS

```
Capital Summary
───────────────────────────────────────
Total capital    : ₹X,XX,XXX
Deployed         : ₹XX,XXX  (XX.X%)
Cash available   : ₹X,XX,XXX  (XX.X%)
Total at risk    : ₹X,XXX  (X.XX%)

Portfolio Metrics (correlation-adjusted)
───────────────────────────────────────
Expected return  : ±X.X% p.a.
Portfolio vol    : X.X% p.a.
Portfolio Sharpe : X.XXX
Portfolio beta   : X.XXX
Divs. ratio      : X.XXX×
```

Sector allocation table — flag any sector > 30% with ⚠:
```
Sector                  Deployed    % Capital   Status
──────────────────────────────────────────────────────
[Sector 1]              ₹XX,XXX     XX.X%       ✅ / ⚠
[Sector 2]              ₹XX,XXX     XX.X%       ✅ / ⚠
Cash                    ₹X,XX,XXX   XX.X%
```

---

#### 3. SCENARIO ANALYSIS

```
Scenario              P&L          % Capital    Interpretation
──────────────────────────────────────────────────────────────────
Bear (all SL hit)     ₹−X,XXX      −X.XX%       [low/moderate/high pain]
Bull T1 (all hit)     ₹+X,XXX      +X.XX%       [expected outcome]
Bull T2 (all hit)     ₹+X,XXX      +X.XX%       [best case]
Expected value        ₹+X,XXX                   [positive = edge present]
```

Write one sentence interpreting the bear scenario: "If all stop losses trigger simultaneously, maximum portfolio loss is ₹X,XXX (X.X%) — within the 2% per-trade risk rule."

---

#### 4. UNIVERSE SNAPSHOT

One-line summary for every ticker scanned, including those with NO_TRADE:

```
Ticker      Sector              Fund   Tech   Signal       Decision
──────────────────────────────────────────────────────────────────────
INFY        Technology          X.X    X.X    SWING_BUY    APPROVED ✅
HDFCBANK    Financial Services  X.X    X.X    WATCH        APPROVED_REDUCED ⏳
TCS         Technology          X.X    X.X    NO_TRADE     REJECTED ❌
BAJAJ-AUTO  Consumer Cyclical   X.X    X.X    WATCH        APPROVED ⏳
```

---

#### 5. TOMORROW'S ACTION PLAN

Numbered, specific, actionable:

1. **[TICKER] — PLACE LIMIT BUY** at ₹X,XXX.XX (before 9:20 AM IST)
   Set stop loss at ₹X,XXX.XX (GTT order) and target at ₹X,XXX.XX
   Position size: X shares | Max outlay: ₹X,XXX

2. **[TICKER] — SET PRICE ALERT** at ₹X,XXX (WATCH signal, not actionable yet)

3. **[TICKER] — NO ACTION** (rejected: [reason])

If no orders: "No orders to place tomorrow. Maintain existing stops and targets."

---

#### 6. RISK EVENTS (Next 5 Trading Days)

Check `references/risk-calendar.md` if it exists. If not, note:
- Any tickers in the portfolio with earnings within 5 trading days: "⚠ Earnings risk — consider exiting before announcement or reducing size by half"
- RBI policy meeting, budget dates if within window
- If no known events: "No major scheduled risk events identified in next 5 trading days."

---

### DISCLAIMER

> ⚠️ SwingTradeIQ is for educational purposes only as part of IBS India MBA — Advanced Business Analytics. All signals are generated by an automated algorithm and are not SEBI-registered investment advice. Scores, signals, and trade levels reflect historical data and model assumptions — not guaranteed future outcomes. Consult a SEBI-registered investment advisor before placing any actual trades. Capital at risk.
