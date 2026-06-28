# SwingTradeIQ — Kelly Criterion Reference

---

## Origin

The Kelly Criterion was derived by John L. Kelly Jr. at Bell Labs in 1956,
originally for signal noise problems in telecommunications. Ed Thorp (MIT
mathematician and blackjack legend) adapted it for financial markets in the 1960s.
It answers one question: **given your edge, what fraction of capital should you bet?**

---

## The Formula

```
f* = (b × p − q) / b

Where:
  f* = fraction of capital to wager
  b  = net odds (avg win / avg loss ratio)
  p  = probability of winning
  q  = probability of losing = 1 − p
```

**Simplified for trading:**
```
f* = (Win Rate × Avg Win %) − ((1 − Win Rate) × Avg Loss %)
     ÷ Avg Win %
```

**Example:**
- Win rate = 60%, Avg win = 6.5%, Avg loss = 3.2%
- f* = (0.60 × 0.065 − 0.40 × 0.032) / 0.065
- f* = (0.039 − 0.0128) / 0.065
- f* = 0.0262 / 0.065
- f* = **0.403 → 40.3% of capital in one trade**

Full Kelly (40.3%) is too aggressive for most traders. One bad streak
causes catastrophic drawdown. SwingTradeIQ uses **Half-Kelly (0.5 × f*)**.

---

## Why Half-Kelly?

| Kelly Fraction | Behaviour |
|---|---|
| Full Kelly | Maximises long-run growth rate mathematically. Extreme volatility. |
| Half-Kelly | ~75% of full Kelly growth rate. Half the volatility. Practical. |
| Quarter-Kelly | Very conservative. Suitable for early-stage (few data points). |

Half-Kelly is the standard recommendation for retail traders. It builds
wealth nearly as fast as full Kelly but with far less psychological pain
during drawdown periods.

---

## How SwingTradeIQ Uses Kelly

The PositionSizerAgent uses a **risk-adjusted Kelly** that combines two constraints:
1. Kelly fraction (based on historical win rate)
2. Fixed risk limit (never more than `risk_per_trade` % of capital)

The binding constraint (whichever gives smaller position) is applied.
This means early in use (few trade history points), the fixed risk limit
usually binds. Once 20+ trades are logged, Kelly calibration takes over.

---

## Calibration

Kelly recalibrates automatically from `performance_log.csv` after every
closed trade (if N ≥ 20). The weekly review report shows:

```
Kelly Calibration:
  Trades used: 23
  Win rate: 61% (assumed: 55%)
  Avg win: 5.8% (assumed: 6.5%)
  Avg loss: 2.9% (assumed: 3.2%)
  Full Kelly: 38.4%  →  Half Kelly: 19.2%
  Current config risk_per_trade: 2.0%
  ↳ Fixed risk constraint is binding (Kelly says more, config caps it)
  Recommendation: Consider raising risk_per_trade to 2.5% given edge confirmed.
```

---

## Edge Cases and Warnings

**Negative Kelly (f* < 0):** Your strategy has no edge. Do not trade.
This happens when avg_loss > avg_win × (win_rate / loss_rate).
SwingTradeIQ will output: "No positive edge detected. Review strategy."

**Kelly > 1 (f* > 100%):** Mathematically says "bet everything + borrow more."
This is a data error or overfitting. SwingTradeIQ caps at f* = 0.25 (25%).

**Insufficient history (< 20 trades):** Kelly is unreliable on small samples.
Use conservative defaults: win_rate = 0.55, avg_win = 0.065, avg_loss = 0.032.

**Changing market regimes:** A win rate estimated during a bull market
overstates edge in bear/sideways markets. Review calibration every quarter.
