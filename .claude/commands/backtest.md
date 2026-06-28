Run a SwingTradeIQ backtest and present an elaborate performance report.

## Step 1 — Parse user input

Extract from the user's message:
- **Start date**: look for patterns like "2023-01-01", "Jan 2023", "last year", "2 years". Convert to YYYY-MM-DD. If missing, default to one year ago.
- **End date**: same patterns. If missing, default to today minus 1 day.
- **Capital**: "2 lakhs" → 200000, "1.5 lakh" → 150000, "50k" → 50000. If missing, read from config.yaml.
- **Tickers**: if the user names specific tickers, use --tickers. Otherwise omit (use config.yaml watchlist).

## Step 2 — Run the engine

```bash
source venv/bin/activate
python swingtrade_iq.py --mode backtest --start START_DATE --end END_DATE --capital CAPITAL
```

If swingtrade_iq.py is not found, say: "BacktestEngine is built in Session 10 — run the full scan first."
If the run fails, show the error and suggest checking the venv and internet connection.

## Step 3 — Read results

Read the JSON output at:
  data/backtest/backtest_START_END.json

Extract these sections: `config`, `metrics`, `benchmark`, `trades`.

## Step 4 — Present the elaborate backtest report

Format the full report in this exact structure, section by section:

---

### BACKTEST REPORT — [TICKERS] | [START] → [END]

> Capital: ₹[CAPITAL] | Universe: [N] stocks | Cost: 0.2% per leg (0.4% round-trip) | Signal: MACD cross + RSI[30–70] + Price>SMA50 | ATR stop: 2×, Target: 4×

---

#### 1. PERFORMANCE SNAPSHOT

Print a clean aligned table:

```
Metric                    Strategy      NIFTY50      Edge
────────────────────────────────────────────────────────
Total Return (%)          +X.XX         +X.XX        +/−X.XX
CAGR (%)                  +X.XX         +X.XX        +/−X.XX
Sharpe Ratio              X.XXX         X.XXX        +/−X.XXX
Max Drawdown (%)          −X.XX         −X.XX        +/−X.XX
Calmar Ratio              X.XXX         —            —
Win Rate (%)              XX.X          —            —
Profit Factor             X.XXX         —            —
Avg R-Multiple            +X.XXX        —            —
Avg Hold (days)           XX.X          —            —
Total Trades              NN            —            —
```

After the table, write a 2–3 sentence plain-English interpretation:
- Did the strategy beat the benchmark? On which dimension (return vs risk)?
- Was the low drawdown a worthwhile trade-off for lower returns?
- Is the profit factor > 1.25 a sign of genuine edge or small sample?

---

#### 2. TRADE BREAKDOWN

Print two sub-sections:

**a) By exit reason** — count, P&L, avg hold:
```
Exit Reason      Count    %     Total P&L    Avg P&L   Avg Hold
──────────────────────────────────────────────────────────────
TARGET1          NN       XX%   ₹XX,XXX      ₹X,XXX    XX.Xd
STOP             NN       XX%   ₹−XX,XXX     ₹−X,XXX   XX.Xd
TIMEOUT          NN       XX%   ₹±XX,XXX     ₹±X,XXX   XX.Xd
STOP_GAPDOWN     NN       XX%   ₹−XX,XXX     ₹−X,XXX   XX.Xd
```

**b) Win/Loss distribution:**
```
                  Winners    Losers
Count             NN         NN
Avg P&L (₹)       +X,XXX     −X,XXX
Best/Worst (₹)    +X,XXX     −X,XXX
Avg R              +X.XX      −X.XX
Avg hold (days)    XX.X       XX.X
```

Note: if STOP_GAPDOWN count > 15% of total trades, add a warning: "High gap-down rate suggests overnight risk — consider exit-at-close on volatile tickers."

---

#### 3. PER-TICKER DEEP DIVE

For each ticker in the results:
```
Ticker       Trades  Wins  Win%   Gross P&L     Best Trade    Worst Trade   Avg Hold
────────────────────────────────────────────────────────────────────────────────────
XXXX         NN      NN    XX.X%  ₹±XX,XXX      ₹+X,XXX       ₹−X,XXX      XX.Xd
```

After the table:
- Flag the best-performing ticker (highest net P&L) with "⭐ Top performer"
- Flag the worst-performing ticker with a reason: "HDFCBANK dragged the portfolio — only 1 of 8 trades hit target, suggesting MACD signals on this ticker are weaker in this period."
- Suggest which tickers to keep, put on watch, or drop from the universe based on win rate (< 33% = drop, 33–50% = watch, > 50% = keep)

---

#### 4. SIGNAL QUALITY ANALYSIS

```
Ticker      Signals Generated   Trades Taken   Conversion%   Avg ATR% at Signal
──────────────────────────────────────────────────────────────────────────────
INFY        NN                  NN             XX.X%         X.XX%
HDFCBANK    NN                  NN             XX.X%         X.XX%
TCS         NN                  NN             XX.X%         X.XX%
BAJAJ-AUTO  NN                  NN             XX.X%         X.XX%
```

Note: Conversion% < 100% means some signals were skipped due to capital constraints or max concurrent positions. If skipped signals were on tickers that later performed well, note: "Missed [N] BAJAJ-AUTO signals due to full position book — consider raising max_concurrent from 4 to 5."

---

#### 5. COST IMPACT ANALYSIS

```
Round-trip cost per trade: 0.4% of notional
──────────────────────────────────────────────
Total notional traded:     ₹XX,XX,XXX
Gross P&L (before cost):   ₹±XX,XXX (estimate)
Cost drag (0.4% × trades): ₹XX,XXX
Net P&L (after cost):      ₹±XX,XXX
Cost as % of gross:        XX.X%
```

Calculate estimated gross P&L as: net_P&L + (0.004 × avg_notional_per_trade × total_trades).
If cost drag > 20% of gross profit, warn: "Transaction costs consumed > 20% of gross profit. Consider: (1) raising entry threshold to reduce trade frequency, (2) using broader targets (6× ATR) to increase avg win."

---

#### 6. KELLY CALIBRATION CHECK

Using results from the backtest:
```
Metric                  Backtest Value   Assumed (live)   Status
──────────────────────────────────────────────────────────────
Win rate                XX.X%            50%              ✅/⚠/❌
Avg win / Avg loss      X.XX             2.0 (2:1 R/R)   ✅/⚠/❌
Historical Kelly        XX.X%            —                —
Trade Kelly (at 2:1)    XX.X%            —                —
Half-Kelly applied      XX.X%            —                —
```

Interpretation rules:
- Win rate 45–55%: "Kelly assumptions are well-calibrated — live sizing is appropriate."
- Win rate 35–45%: "Win rate below assumed 50%. Reduce kelly_fraction in config.yaml from 0.5 to 0.4."
- Win rate < 35%: "Win rate significantly below model assumption. Suggest reducing position size or tightening entry filters."
- Avg win / Avg loss < 1.5: "R/R below 2:1 target. Check if ATR stop multiplier needs tightening."

---

#### 7. DRAWDOWN ANALYSIS

```
Max Drawdown:        −X.XX%  (₹X,XXX)
Max DD vs Benchmark: NIFTY50 had −X.XX% — strategy preserved X.X% more capital at trough
Recovery:            [Estimate from equity curve if available, else "see HTML report"]
Risk of Ruin:        [if max_dd > 20%: HIGH, 10–20%: MEDIUM, < 10%: LOW]
```

If max drawdown < 5%: "The strategy's low drawdown (< 5%) reflects its conservative entry filters and small position sizes. Main cost: missed the NIFTY50 bull run."
If max drawdown 5–15%: "Moderate drawdown within acceptable swing trading range."
If max drawdown > 15%: "Drawdown exceeded 15% — review stop loss multiplier (currently 2×ATR). Consider tightening to 1.5×ATR."

---

#### 8. ACTIONABLE INSIGHTS

List 3–5 specific, numbered recommendations derived from the actual numbers:

Example format:
1. **Raise max_concurrent to 5** — BAJAJ-AUTO had [N] missed signals due to full position book, and its win rate was [XX]%. More concurrent positions would have captured this alpha.
2. **Drop HDFCBANK from watchlist for this signal type** — 12.5% win rate over 8 trades is below the 33% edge threshold for a 2:1 R/R system. MACD crossover does not work well for this ticker in the given period.
3. **Extend timeout from 20 to 25 days** — [N] TIMEOUT exits had a mixed record. Some winners may have been cut early.
4. **Consider asymmetric targets** — currently T1=4×ATR, T2=6×ATR. With a 45% win rate, consider trailing stop after T1 to let winners run further.
5. **Re-run with 3-year window** — 2 years may not be enough to capture full market cycles (bull + bear + sideways). Suggest `--start 2022-01-01 --end 2024-12-31`.

Generate recommendations based on actual numbers, not the examples above.

---

#### 9. HTML REPORT

Always tell the user:
> 📄 Full interactive report with equity curve chart saved to:
> `outputs/backtest_[START]_[END].html`
> Open in any browser. Contains embedded benchmark chart, full trade log, and all metrics.

---

### DISCLAIMER

> ⚠️ SwingTradeIQ is for educational purposes only as part of IBS India MBA — Advanced Business Analytics. Backtest results are hypothetical and do not account for market impact, liquidity constraints, corporate actions, or circuit breaker halts. Past simulated performance is not indicative of future results. Not SEBI-registered investment advice. Consult a registered investment advisor before deploying capital.
