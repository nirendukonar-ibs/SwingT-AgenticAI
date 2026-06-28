# SwingTradeIQ — Risk Event Calendar

High-impact events where position sizes should be reduced or new entries avoided.
Updated: June 2025. Refresh this file at start of each month.

---

## Event Risk Guidelines

| Event Type | Recommended Action |
|---|---|
| RBI Monetary Policy | Reduce all positions by 30% day before. No new entries. |
| Union Budget | Reduce by 50% week before. Sit out Budget day entirely. |
| US Federal Reserve (FOMC) | Reduce by 20% on decision day (night IST) |
| Major Index Rebalancing | Flag affected stocks — unusual volume distorts signals |
| Quarterly Earnings (held stock) | Exit or reduce before announcement |
| GST Council Meeting | Sector-specific risk (FMCG, Auto, Real Estate) |
| State Elections (large states) | Reduce PSU, infrastructure stocks |
| Global macro shock | Manual override — run monitor mode, tighten stops |

---

## Recurring Calendar — FY 2025-26

### RBI Monetary Policy Committee (MPC) Meetings
Dates announced ~2 months in advance. Decisions released ~10:00 AM IST.

| Meeting | Decision Date |
|---|---|
| MPC 1 | 4–6 June 2025 |
| MPC 2 | 5–7 August 2025 |
| MPC 3 | 29 Sep – 1 Oct 2025 |
| MPC 4 | 4–6 December 2025 |
| MPC 5 | February 2026 (TBD) |
| MPC 6 | April 2026 (TBD) |

### Union Budget
- Interim Budget: February 1, 2025 (done)
- Full Budget: July 2025 (TBD — watch for announcement)

### US FOMC Meetings (remaining 2025)
| Meeting | Decision Date (IST) |
|---|---|
| July | 30 July 2025 (late night IST) |
| September | 17 Sep 2025 |
| November | 6 Nov 2025 |
| December | 17 Dec 2025 |

### Nifty 50 Index Rebalancing
NSE rebalances semi-annually. Next: ~October 2025.
Stocks being added/removed show unusual volume — exclude from signals week of rebalancing.

---

## Quarterly Earnings Season (NSE)

Results announced 45 days after quarter end. Heavy flow period:
- Q1 FY26 (Apr–Jun): July 15 – August 15, 2025
- Q2 FY26 (Jul–Sep): October 15 – November 15, 2025
- Q3 FY26 (Oct–Dec): January 15 – February 15, 2026
- Q4 FY26 (Jan–Mar): April 15 – May 15, 2026

During earnings season: PositionSizerAgent auto-reduces size for stocks
with results due within 5 days. Risk classification elevated to HIGH.

---

## NSE Holidays 2025

| Date | Holiday |
|---|---|
| 26 Jan | Republic Day |
| 14 Feb | Mahashivratri (if applicable) |
| 17 Mar | Holi |
| 14 Apr | Dr. Ambedkar Jayanti / Ram Navami |
| 18 Apr | Good Friday |
| 01 May | Maharashtra Day |
| 15 Aug | Independence Day |
| 27 Aug | Ganesh Chaturthi |
| 02 Oct | Gandhi Jayanti |
| 02 Oct | Dussehra (if applicable) |
| 20 Oct | Diwali Laxmi Pujan |
| 21 Oct | Diwali Balipratipada |
| 05 Nov | Guru Nanak Jayanti |
| 25 Dec | Christmas |

*Verify against NSE official holiday list annually. File: `data/universe/nse_holidays_2025.csv`*

---

## How the System Uses This File

RiskAgent reads this file and:
1. Checks if any HIGH-IMPACT event falls within next 5 trading days
2. If yes → reduces recommended position sizes by 20–50% (event-dependent)
3. Adds event warning block to report output
4. On Budget/MPC day itself → blocks new entry signals entirely

To override (if you want to trade despite event risk):
```bash
python swingtrade_iq.py --mode scan --ignore-risk-calendar
```
Use with caution.
