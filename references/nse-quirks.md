# SwingTradeIQ — NSE-Specific Quirks

Critical India/NSE-specific behaviours the system must handle correctly.

---

## Ticker Format

All NSE tickers must have `.NS` suffix for yfinance:
```
RELIANCE   →  RELIANCE.NS
HDFCBANK   →  HDFCBANK.NS
BAJAJ-AUTO →  BAJAJ-AUTO.NS    ← hyphen preserved
M&M        →  M%26M.NS         ← ampersand URL-encoded (use MM.NS instead)
```

BSE tickers use `.BO` suffix. SwingTradeIQ defaults to NSE (`.NS`).
If `.NS` fetch fails, retry with `.BO` automatically.

---

## Circuit Breakers

NSE applies daily price bands (circuit filters) on individual stocks:
- 2%, 5%, 10%, or 20% bands depending on stock category
- Index-level circuits: 10%, 15%, 20% trigger market-wide halts

**Impact on stop losses:**
If a stock hits lower circuit, sell orders queue but may not execute.
Stop loss price may not be achievable — actual exit price could be worse.

**SwingTradeIQ handling:**
- RiskAgent flags stocks with < 10% circuit band as HIGH risk
- Stop loss for circuit-prone stocks widened to 0.8 × circuit band
- ReportAgent adds circuit warning to thesis when applicable

---

## Settlement: T+1

NSE moved to T+1 settlement (from T+2) in 2023.
- Shares bought today are available to sell the next trading day
- Funds from sales are available next trading day

**Impact:** No same-day round trips (buy and sell same stock same day)
in cash segment. Intraday is separate (MIS orders, different margins).
SwingTradeIQ is for cash segment swing trades — minimum 1 overnight hold.

---

## Trading Hours

| Session | Time (IST) |
|---|---|
| Pre-open order collection | 9:00 AM – 9:08 AM |
| Pre-open price discovery | 9:08 AM – 9:15 AM |
| **Regular market open** | **9:15 AM** |
| **Regular market close** | **3:30 PM** |
| Post-close session | 3:40 PM – 4:00 PM |

SwingTradeIQ runs after 4:00 PM to ensure full-day data is available.
Orders placed at pre-open (9:00–9:08 AM) get priority at discovered price.
**Recommended:** Place limit orders during pre-open session for better fills.

---

## Nifty 500 Universe

File: `data/universe/nifty500_tickers.csv` — updated quarterly.
Source: NSE official constituent list (https://www.nseindia.com).

Sectors used in concentration limits (NSE classification):
AUTOMOBILE, BANK, CAPITAL_GOODS, CHEMICALS, CONSUMER_DURABLES,
FMCG, FINANCIAL_SERVICES, HEALTHCARE, IT, MEDIA, METAL,
OIL_GAS, PHARMA, POWER, PSU_BANK, REALTY, TELECOM, TEXTILE

---

## FII/DII Data

Foreign Institutional Investor (FII) and Domestic Institutional Investor (DII)
daily activity data is published by NSE after market close.
Not currently used in SwingTradeIQ v1 — planned for v2 OBV enhancement.

---

## Holidays

NSE trading holidays for 2025 (hardcoded in `data/universe/nse_holidays_2025.csv`):
System checks this list before running — skips run on holidays with message:
"NSE closed today ([holiday name]). No data to process."

Update this file annually at year start.

---

## Corporate Actions Adjustment

yfinance `auto_adjust=True` handles splits and dividends automatically.
However, bonus issues and rights issues may not always be captured correctly.
QualityValidatorAgent flags single-day price moves > 20% for manual review —
these are usually unadjusted corporate actions in the raw data.

---

## Common Data Issues

| Issue | Symptom | Fix applied |
|---|---|---|
| Missing last trading day | Latest row is T-2 not T-1 | Flag as STALE, skip ticker |
| Zero volume rows | Volume = 0 on non-holiday | Forward-fill price, flag |
| Penny stock noise | Price < ₹10, erratic moves | Excluded by min_market_cap filter |
| Newly listed stocks | < 100 rows of history | Excluded by minimum history check |
| Suspended stocks | No data at all from yfinance | Logged in fetch_errors.txt |
