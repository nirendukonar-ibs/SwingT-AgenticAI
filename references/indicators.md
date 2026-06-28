# SwingTradeIQ — Indicator Reference

Formulas, thresholds, and interpretation guides for all five indicators
used in the IndicatorEngine. All computed using the `ta` Python library.

---

## RSI — Relative Strength Index

**Formula:**
```
RS  = Average Gain over N periods / Average Loss over N periods
RSI = 100 − (100 / (1 + RS))
```
**Period used:** 14 days (Wilder smoothing)

**SwingTradeIQ thresholds:**
| RSI Value | Interpretation | Score |
|---|---|---|
| < 30 | Oversold — potential reversal BUT could be downtrend continuation | 0 |
| 30–45 | Recovery zone — watch for momentum | 0 |
| 45–65 | **Bullish momentum zone — ideal swing entry** | +1 |
| 65–70 | Extended but not overbought | 0 |
| > 70 | Overbought — avoid new longs | 0 |

**Why 45–65 for swing trades:** RSI < 45 means momentum hasn't turned.
RSI > 65 means you're buying late. The 45–65 window catches stocks with
confirmed upward momentum before they become crowded trades.

---

## MACD — Moving Average Convergence Divergence

**Formula:**
```
MACD Line   = 12-day EMA − 26-day EMA
Signal Line = 9-day EMA of MACD Line
Histogram   = MACD Line − Signal Line
```
**Parameters used:** (12, 26, 9) — standard settings

**SwingTradeIQ logic:**
- **Bullish crossover:** MACD line crosses above signal line
- Valid only if crossover happened within last 3 trading days
- Older crossovers score 0 (signal has decayed — late entry risk)
- **Histogram check:** Histogram should be turning positive (not just crossed)

**Additional filter:** MACD lines should be below zero or near zero.
Crossover at elevated MACD values (stock already extended) scores 0.

---

## Bollinger Bands

**Formula:**
```
Middle Band = 20-day SMA
Upper Band  = Middle Band + (2 × 20-day StdDev)
Lower Band  = Middle Band − (2 × 20-day StdDev)
Band Width  = (Upper − Lower) / Middle
```
**Parameters used:** (20, 2)

**SwingTradeIQ scoring:**
| Condition | Score |
|---|---|
| Price touched/crossed lower band in last 5 days AND now recovering | +1 |
| Band width expanding (current BW > 20-day avg BW) | +1 |
| Price at upper band | 0 |
| Bands contracting (squeeze) — breakout imminent but direction unknown | 0 |

**Bollinger Squeeze:** When band width < 80th percentile of last 6 months,
a squeeze is forming. Score 0 but flag as WATCH — enter after breakout
direction confirmed (price exits band with volume).

---

## ADX — Average Directional Index

**Formula:**
```
+DM = current high − previous high (if positive, else 0)
−DM = previous low − current low (if positive, else 0)
TR  = max(high−low, |high−prev_close|, |low−prev_close|)
+DI = 100 × EMA(+DM, 14) / EMA(TR, 14)
−DI = 100 × EMA(−DM, 14) / EMA(TR, 14)
DX  = 100 × |+DI − −DI| / (+DI + −DI)
ADX = EMA(DX, 14)
```
**Period used:** 14 days

**SwingTradeIQ thresholds:**
| ADX Value | Market condition | Score |
|---|---|---|
| < 20 | Ranging / trendless — indicators unreliable | 0 |
| 20–25 | Weak trend forming | 0 |
| **> 25** | **Trend confirmed — swing trades work** | +1 |
| > 40 | Strong trend — but may be late to enter | +1 (but flag) |
| > 60 | Extreme trend — counter-trend risk high | 0 |

**Note:** ADX measures trend STRENGTH, not direction. A falling ADX = trend
weakening regardless of price direction. Always pair with +DI/−DI to confirm
bullish vs bearish trend.

---

## OBV — On-Balance Volume

**Formula:**
```
If close > prev_close: OBV = prev_OBV + volume
If close < prev_close: OBV = prev_OBV − volume
If close = prev_close: OBV = prev_OBV
```

**SwingTradeIQ logic:**
OBV is used for **divergence detection**, not absolute value.

| Condition | Score |
|---|---|
| OBV making higher highs alongside price higher highs | +1 |
| OBV flat or declining while price rising (bearish divergence) | 0 (warning flag) |
| OBV rising while price flat (accumulation) | +1 |
| OBV declining alongside price (distribution) | 0 |

**Divergence flag:** If OBV diverges bearishly (price up, OBV down), the
ReportAgent adds a warning to the thesis even if total score ≥ 3.

---

## Composite Score Interpretation

| Score | Tag | Action |
|---|---|---|
| 5/5 | STRONG BUY | Enter with standard size. Rare — verify manually. |
| 4/5 | HIGH | Enter with standard size |
| 3/5 | MODERATE | Enter with 75% of standard size |
| 2/5 | WATCH | Add to watchlist, do not enter |
| 0–1/5 | AVOID | Skip entirely |

**Important:** Score is a setup quality measure, not a price target predictor.
A 5/5 score means the setup is technically sound — outcomes still depend on
market conditions, news flow, and position management.
