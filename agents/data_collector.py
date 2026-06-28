# Cell 7.1 — Complete DataCollectorAgent — save this to agents/data_collector.py
# This is the authoritative version. All previous cells were building toward this.

import os, time, json, warnings
from pathlib import Path
from datetime import datetime, timedelta
import yfinance as yf
import pandas as pd
warnings.filterwarnings('ignore')


class DataCollectorAgent:
    """
    Agent 1/10 — SwingTradeIQ Data Collection Agent.

    Fetches OHLCV market data and company metadata for a watchlist
    of NSE-listed stocks and saves structured outputs for downstream agents.

    Usage:
        agent = DataCollectorAgent('/path/to/SwingTradeIQ')
        summary = agent.run(['INFY', 'HDFCBANK', 'RELIANCE'])

    Outputs:
        data/raw/{TICKER}_ohlcv.csv    — price history per stock
        data/meta/universe_meta.json   — company metadata for all stocks
        logs/fetch_{DATE}.txt          — run log
    """

    MIN_ROWS = 100

    META_FIELDS = [
        'shortName', 'sector', 'industry', 'marketCap',
        'trailingPE', 'priceToBook', 'returnOnEquity', 'debtToEquity',
        'revenueGrowth', 'operatingCashflow', 'totalDebt', 'currentRatio',
        'sharesOutstanding', 'grossMargins', 'beta',
        'fiftyTwoWeekHigh', 'fiftyTwoWeekLow', 'averageVolume',
        'dividendYield', 'currency',
    ]

    def __init__(self, base_dir, period_days=365):
        self.base_dir    = Path(base_dir)
        self.raw_dir     = self.base_dir / 'data' / 'raw'
        self.meta_dir    = self.base_dir / 'data' / 'meta'
        self.log_dir     = self.base_dir / 'logs'
        self.period_days = period_days
        self.failed      = []
        self.succeeded   = []
        for d in [self.raw_dir, self.meta_dir, self.log_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def _add_ns_suffix(self, ticker: str) -> str:
        ticker = ticker.strip().upper()
        return ticker if '.' in ticker else ticker + '.NS'

    def fetch_ohlcv(self, ticker: str) -> pd.DataFrame | None:
        ticker    = self._add_ns_suffix(ticker)
        end_date  = datetime.today()
        start_date = end_date - timedelta(days=self.period_days)

        for attempt in range(2):
            try:
                df = yf.download(
                    ticker,
                    start=start_date.strftime('%Y-%m-%d'),
                    end=end_date.strftime('%Y-%m-%d'),
                    auto_adjust=True, progress=False, threads=False,
                )
                if df.empty:
                    if attempt == 0: time.sleep(2); continue
                    self.failed.append({'ticker': ticker, 'reason': 'empty response'})
                    return None

                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
                df.index.name = 'Date'

                if len(df) < self.MIN_ROWS:
                    self.failed.append({'ticker': ticker, 'reason': f'insufficient history ({len(df)} rows)'})
                    return None

                out_path = self.raw_dir / f"{ticker.replace('.NS','').replace('.BO','')}_ohlcv.csv"
                df.to_csv(out_path)
                self.succeeded.append(ticker)
                return df

            except Exception as e:
                if attempt == 0: time.sleep(3); continue
                self.failed.append({'ticker': ticker, 'reason': str(e)})
                return None
        return None

    def fetch_meta(self, ticker: str) -> dict:
        ticker       = self._add_ns_suffix(ticker)
        ticker_clean = ticker.replace('.NS','').replace('.BO','')
        try:
            info = yf.Ticker(ticker).info
            meta = {'ticker': ticker, 'ticker_clean': ticker_clean, 'fetched_at': datetime.now().isoformat()}
            for field in self.META_FIELDS:
                meta[field] = info.get(field, None)
            return meta
        except Exception as e:
            return {'ticker': ticker, 'ticker_clean': ticker_clean, 'error': str(e)}

    def run(self, watchlist: list[str], delay_seconds: float = 0.5) -> dict:
        start_time = time.time()
        all_meta   = []

        print(f"{'='*55}\nDataCollectorAgent.run() — {len(watchlist)} stocks\n{'='*55}")

        for i, ticker in enumerate(watchlist, 1):
            ticker_ns = self._add_ns_suffix(ticker)
            print(f"[{i:>3}/{len(watchlist)}] {ticker_ns:<18}", end='', flush=True)
            df = self.fetch_ohlcv(ticker)
            if df is None:
                print("  ❌ FAILED")
                time.sleep(delay_seconds)
                continue
            meta = self.fetch_meta(ticker)
            all_meta.append(meta)
            print(f"  ✅ {len(df)} rows | ₹{df['Close'].iloc[-1]:.2f} | {meta.get('sector','Unknown') or 'Unknown'}")
            time.sleep(delay_seconds)

        meta_path = self.meta_dir / 'universe_meta.json'
        with open(meta_path, 'w') as f:
            json.dump(all_meta, f, indent=2, default=str)

        elapsed  = time.time() - start_time
        log_path = self.log_dir / f"fetch_{datetime.now().strftime('%Y-%m-%d')}.txt"
        with open(log_path, 'w') as f:
            f.write('\n'.join([
                f"SwingTradeIQ DataCollectorAgent",
                f"Date    : {datetime.now().strftime('%Y-%m-%d %H:%M IST')}",
                f"Duration: {elapsed:.1f}s",
                f"",
                f"SUCCEEDED ({len(self.succeeded)}):",
                *[f"  {t}" for t in self.succeeded],
                f"\nFAILED ({len(self.failed)}):",
                *[f"  {x['ticker']} — {x['reason']}" for x in self.failed],
            ]))

        print(f"\n{'='*55}")
        print(f"Done in {elapsed:.1f}s | ✅ {len(self.succeeded)} | ❌ {len(self.failed)}")
        print(f"{'='*55}")
        return {
            'succeeded': self.succeeded, 'failed': self.failed,
            'ohlcv_dir': str(self.raw_dir), 'meta_path': str(meta_path),
            'log_path': str(log_path), 'elapsed_s': round(elapsed, 1),
        }


# ── Save to agents/data_collector.py ─────────────────────────────────────────
import inspect
agent_source = inspect.getsource(DataCollectorAgent)
agent_file   = BASE_DIR / 'agents' / 'data_collector.py'

with open(agent_file, 'w') as f:
    f.write("# SwingTradeIQ — Agent 1/10: DataCollectorAgent\n")
    f.write("# Built in Session 1. Do not edit during later sessions.\n")
    f.write("# Handoff: produces data/raw/*_ohlcv.csv + data/meta/universe_meta.json\n\n")
    f.write("import os, time, json, warnings\n")
    f.write("from pathlib import Path\n")
    f.write("from datetime import datetime, timedelta\n")
    f.write("import yfinance as yf\n")
    f.write("import pandas as pd\n")
    f.write("warnings.filterwarnings('ignore')\n\n\n")
    f.write(agent_source)

print(f"\n✅ Agent saved to: {agent_file}")
print(f"   Size: {agent_file.stat().st_size:,} bytes")
print("\nReady for Session 2 — QualityValidatorAgent will read from data/raw/")