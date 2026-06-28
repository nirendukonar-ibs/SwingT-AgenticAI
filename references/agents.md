# SwingTradeIQ — Agent Reference

Each agent is a Python class in `~/SwingTradeIQ/agents/`. This file documents
what each agent does, its inputs, outputs, and key logic.

---

## 1. DataCollectorAgent

**File:** `agents/data_collector.py`
**Role:** Fetches raw market data for all watchlist stocks.

**Inputs:**
- Ticker list (from config watchlist)
- Date range (default: last 200 trading days for indicators + today's close)

**Tools used:**
- `yfinance.download()` — OHLCV data with `.NS` suffix for NSE
- `yfinance.Ticker().info` — market cap, sector, fundamentals

**Outputs:**
- `data/raw/[TICKER]_ohlcv.csv` per stock
- `data/meta/universe_meta.json` — sector, market cap, name for each ticker

**Key logic:**
- Appends `.NS` to all tickers automatically if not present
- Fetches 200 days of history (needed for 200-DMA and long-term indicators)
- Retries failed tickers once after 30-second wait
- Logs all failures to `data/raw/fetch_errors.txt`

**NSE-specific notes:** See `references/nse-quirks.md`

---

## 2. QualityValidatorAgent

**File:** `agents/quality_validator.py`
**Role:** Audits raw data and flags stocks unsuitable for analysis.

**Inputs:** All CSVs from DataCollectorAgent

**Checks performed:**
- Missing values > 5% of rows → FAIL
- Zero volume days > 3 in last 20 days → WARN
- Single-day price move > 20% (likely unadjusted corporate action) → FLAG
- Data staleness: last date not within 1 trading day of today → FAIL
- Minimum history: < 100 rows → FAIL (insufficient for indicators)

**Outputs:**
- `data/validated/[TICKER]_clean.csv` — adjusted, validated data
- `outputs/data_quality_[DATE].txt` — pass/warn/fail per ticker

**Auto-fixes applied:**
- Forward-fills up to 2 consecutive missing rows
- Adjusts for splits and dividends using yfinance `auto_adjust=True`

---

## 3. EDAAgent

**File:** `agents/eda_agent.py`
**Role:** Statistical profiling of each stock's behaviour.

**Computed metrics (per stock):**
- Daily return mean, std dev, skewness, kurtosis
- Rolling 20-day volatility (annualised)
- 52-week high/low and current position within range
- Average daily volume (20-day) and volume trend
- Day-of-week return bias (Mon–Fri)
- Correlation with Nifty50 (beta proxy)

**LLM narrative:** Feeds summary stats to Claude with prompt:
> "In 2 sentences, describe this stock's personality for a swing trader.
>  Focus on volatility, trend behaviour, and volume characteristics."

**Output:** `data/eda/[TICKER]_profile.json`

---

## 4. FundamentalAgent

**File:** `agents/fundamental_agent.py`
**Role:** Scores each stock on fundamental health. Acts as a first filter.

**Data source:** `yfinance.Ticker().info` fields

**Metrics computed:**

| Metric | Weight | Threshold |
|---|---|---|
| P/E vs sector median | 20% | Below median = positive |
| Return on Equity (ROE) | 25% | > 15% = positive |
| Debt/Equity ratio | 20% | < 1.0 = positive |
| Revenue growth (YoY) | 15% | > 10% = positive |
| Piotroski F-Score | 20% | ≥ 6/9 = positive |

**Piotroski F-Score components (9 binary signals):**
- Positive ROA, positive CFO, ROA improving, CFO > ROA (4 profitability signals)
- Leverage falling, current ratio rising, no new shares issued (3 leverage signals)
- Gross margin improving, asset turnover improving (2 efficiency signals)

**Fundamental Score = weighted sum → normalised to 1–10**
Stocks scoring < 5.0 are removed from further analysis.

**Output:** `data/fundamental/scores.json`

---

## 5. TechnicalAgent

**File:** `agents/technical_agent.py`
**Role:** Identifies trend bias and key price levels.

**Trend signals computed:**
- Price vs 50-DMA and 200-DMA (above/below)
- Golden Cross / Death Cross detection (50-DMA crossing 200-DMA)
- Swing high/low identification (last 20 days, 5-bar pivot logic)
- Support zone: average of last 3 swing lows
- Resistance zone: average of last 3 swing highs
- Current price position: at support / mid-range / at resistance

**Candlestick pattern detection (last 3 candles):**
- Bullish: Hammer, Bullish Engulfing, Morning Star, Piercing Line
- Bearish: Shooting Star, Bearish Engulfing, Evening Star, Dark Cloud Cover
- Neutral: Doji (flags indecision)

**Trend Bias output:**
- BULLISH: Price > 50-DMA > 200-DMA, at/near support, bullish candle pattern
- BEARISH: Price < 50-DMA, at/near resistance, bearish pattern
- SIDEWAYS: 50-DMA flat, price oscillating within range

**Output:** `data/technical/[TICKER]_technical.json`

---

## 6. IndicatorEngine

**File:** `agents/indicator_engine.py`
**Role:** Generates a consensus signal score from 5 indicators.

**Library used:** `ta` (Technical Analysis library for Python)

**Indicators and scoring logic:**

### RSI (14-period)
- Score +1 if RSI between 45–65 (momentum building, not overbought)
- Score 0 if RSI > 70 (overbought) or RSI < 30 (oversold, potential value trap)
- Threshold source: `references/indicators.md`

### MACD (12, 26, 9)
- Score +1 if MACD line crossed above signal line within last 3 days
- Score 0 if crossover is older than 3 days (signal has decayed) or bearish cross

### Bollinger Bands (20-period, 2 std dev)
- Score +1 if price recently bounced from lower band (within last 5 days)
- Score +1 if band width is expanding (volatility increasing — breakout potential)
- Score 0 if price is at upper band (extended) or bands are contracting

### ADX (14-period)
- Score +1 if ADX > 25 (trending market — swing trades work better in trends)
- Score 0 if ADX < 20 (ranging market — indicator signals unreliable)

### OBV (On-Balance Volume)
- Score +1 if OBV is making higher highs alongside price (volume confirming move)
- Score 0 if OBV diverging (price rising but OBV flat/falling — warning sign)

**Total Indicator Score: 0–5**
Score ≥ 3 → enters candidate list
Score ≥ 4 → HIGH confidence tag
Score 5 → STRONG BUY tag (rare — treat with extra position size discipline)

**Output:** `data/signals/indicator_scores.json`

---

## 7. RiskAgent

**File:** `agents/risk_agent.py`
**Role:** Quantifies downside risk and sets stop loss levels.

**Per-stock calculations:**

**ATR Stop Loss:**
- ATR = Average True Range (14-period)
- Stop Loss = Entry Price − (1.5 × ATR)
- This places stop below normal volatility noise

**Risk/Reward Ratio:**
- Target = nearest resistance level (from TechnicalAgent)
- R:R = (Target − Entry) / (Entry − Stop Loss)
- Minimum acceptable R:R = 1.5 (target must be 1.5× the risk)
- Stocks with R:R < 1.5 are removed from candidate list

**Value at Risk (VaR):**
- 1-day VaR at 95% confidence = 1.645 × daily_std × position_size
- Reported for information only — does not filter candidates

**Risk Classification:**
- LOW: Beta < 0.8, ATR/Price < 2%, no upcoming earnings in 5 days
- MEDIUM: Beta 0.8–1.2, ATR/Price 2–4%
- HIGH: Beta > 1.2, ATR/Price > 4%, OR earnings within 5 days

**Output:** `data/risk/[TICKER]_risk.json`

---

## 8. PositionSizerAgent

**File:** `agents/position_sizer.py`
**Role:** Calculates exact rupee and share quantity for each trade.

**Formula (Half-Kelly, risk-adjusted):**

```
Step 1: Max loss per trade (INR) = Total Capital × risk_per_trade
        e.g. ₹2,00,000 × 0.02 = ₹4,000

Step 2: Risk per share = Entry Price − Stop Loss Price

Step 3: Raw shares = Max loss ÷ Risk per share

Step 4: Position value = Raw shares × Entry Price

Step 5: Kelly adjustment:
        f* = (win_rate × avg_win) − ((1 − win_rate) × avg_loss)
             ÷ avg_win
        Actual fraction used = f* × kelly_fraction (0.5)
        Position value = Position value × min(1.0, actual_fraction ÷ 0.02)

Step 6: Share quantity = round down to nearest whole share
        (No fractional shares on NSE)
```

**Calibration:** Win rate and avg win/loss pulled from last N closed trades
in `performance_log.csv`. Default assumptions used for first 20 trades:
win_rate = 0.55, avg_win = 0.065, avg_loss = 0.032.

**Output per stock:**
```json
{
  "ticker": "BAJFINANCE.NS",
  "entry_price": 7110,
  "stop_loss": 6940,
  "target": 7480,
  "risk_per_share": 170,
  "position_value_inr": 47880,
  "share_quantity": 6,
  "risk_reward_ratio": 2.18
}
```

---

## 9. PortfolioManagerAgent

**File:** `agents/portfolio_manager.py`
**Role:** Applies portfolio-level constraints to the candidate list.

**Constraints applied (in order):**

1. **Max open positions:** If already at limit, only generate EXIT signals
2. **Sector concentration:** If a sector already at 30%, exclude new candidates from same sector
3. **Correlation filter:** If candidate correlation with existing position > 0.75, skip
   (avoids doubling up on similar risk)
4. **Capital availability:** Total of new position sizes must not exceed available capital
5. **Rank by score:** Among remaining candidates, rank by Indicator Score desc, then R:R desc
6. **Select top N:** Where N = max_open_positions − current_open_positions

**Also manages:**
- Trailing stop logic: if position up > 3%, trail stop to breakeven
- Position aging: flag any position held > 15 days for forced review
- Reallocation: when a position exits, flags freed capital for next scan

**Output:** Final ranked candidate list + portfolio state update

---

## 10. ReportAgent

**File:** `agents/report_agent.py`
**Role:** Synthesises all agent outputs into human-readable report.

**Calls Claude API with:**
- All agent JSON outputs for top 3 candidates
- Prompt: "Write a 150-word investment thesis for a swing trade in [STOCK].
  Use the fundamental score, technical bias, indicator score, and risk metrics
  provided. Be specific about why now, what you're watching for, and what
  invalidates the thesis. Tone: professional sell-side analyst."

**Generates:**
- `report_[DATE].json` — structured data (read by this skill)
- `report_[DATE].html` — visual dashboard with charts
- `report_[DATE].pdf` — printable version (via weasyprint)

**HTML report sections:**
1. Executive Summary (top candidates at a glance)
2. Open Positions tracker with P&L
3. Candidate deep-dives (one section per stock)
4. Portfolio composition chart (sector pie, capital bar)
5. Risk events calendar
6. Disclaimer
