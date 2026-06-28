# SwingTradeIQ — UniverseLoader
# Resolves a universe name or CSV path to a clean list of NSE ticker strings.
#
# Sources (in priority order when called from orchestrator):
#   1. --tickers CLI flag  (explicit override, skips this module)
#   2. --universe <name>   (named index: nifty50, nifty100, nifty_midcap150 …)
#   3. --csv <path>        (user-supplied CSV file)
#   4. config.yaml watchlist.csv_file
#   5. config.yaml watchlist.universe
#   6. config.yaml watchlist.custom_tickers
#   7. Built-in fallback:  Nifty 50

import csv
from pathlib import Path

# ── Built-in index compositions ───────────────────────────────────────────────
# Verified against NSEIndia index constituents (July 2025).
# Re-check at https://www.nseindia.com/market-data/live-equity-market
# under "Index" → select index → "Download complete list" every quarter.

_NIFTY50 = [
    'ADANIENT','ADANIPORTS','APOLLOHOSP','ASIANPAINT','AXISBANK',
    'BAJAJ-AUTO','BAJAJFINSV','BAJFINANCE','BHARTIARTL','BPCL',
    'BRITANNIA','CIPLA','COALINDIA','DIVISLAB','DRREDDY',
    'EICHERMOT','ETERNAL','GRASIM','HCLTECH','HDFCBANK',
    'HDFCLIFE','HEROMOTOCO','HINDALCO','HINDUNILVR','ICICIBANK',
    'INDUSINDBK','INFY','ITC','JSWSTEEL','KOTAKBANK',
    'LT','M&M','MARUTI','NESTLEIND','NTPC',
    'ONGC','POWERGRID','RELIANCE','SBILIFE','SBIN',
    'SHRIRAMFIN','SUNPHARMA','TATACONSUM','TATAMOTORS','TATASTEEL',
    'TCS','TECHM','TITAN','TRENT','ULTRACEMCO',
]

# Nifty Next 50 — these 50 + Nifty 50 = Nifty 100
_NIFTY_NEXT50 = [
    'ADANIGREEN','ADANIPOWER','AMBUJACEM','ATGL','AWL',
    'BANKBARODA','BEL','BERGEPAINT','BOSCHLTD','CHOLAFIN',
    'COLPAL','DMART','DABUR','DLF','EIHOTEL',
    'FSN','GODREJCP','GODREJPROP','HAL','HAVELLS',
    'ICICIGI','ICICIPRULI','INDUSTOWER','IRCTC','IRFC',
    'JIOFIN','LUPIN','MARICO','MCDOWELL-N','MFSL',
    'NHPC','NYKAA','OFSS','PIDILITIND','PNB',
    'RECLTD','SIEMENS','SJVN','TATACOMM','TATAPOWER',
    'TORNTPHARM','TORNTPOWER','UNIONBANK','VEDL','VBL',
    'VOLTAS','YESBANK','ZOMATO','ZYDUSLIFE','MANKIND',
]

_NIFTY100    = _NIFTY50 + _NIFTY_NEXT50

# For Nifty 150 / 200 / 250 / 500 — composition changes frequently.
# Download the CSV from NSEIndia and place it in data/universe/:
#   nifty_midcap150.csv   — Nifty Midcap 150
#   nifty200.csv          — Nifty 200  (Nifty 100 + Nifty Midcap 100)
#   nifty500.csv          — Nifty 500  (full 500-stock universe)
#   nifty_smallcap250.csv — Nifty Smallcap 250

BUILT_IN: dict[str, list[str]] = {
    'nifty50':     _NIFTY50,
    'nifty_50':    _NIFTY50,
    'nifty next50':_NIFTY_NEXT50,
    'nifty100':    _NIFTY100,
    'nifty_100':   _NIFTY100,
}

# Aliases that map a friendly name to a CSV filename in data/universe/
CSV_ALIASES: dict[str, str] = {
    'nifty150':          'nifty_midcap150.csv',
    'nifty_midcap150':   'nifty_midcap150.csv',
    'nifty200':          'nifty200.csv',
    'nifty_200':         'nifty200.csv',
    'nifty250':          'nifty250.csv',
    'nifty_250':         'nifty250.csv',
    'nifty300':          'nifty300.csv',
    'nifty_300':         'nifty300.csv',
    'nifty350':          'nifty350.csv',
    'nifty_350':         'nifty350.csv',
    'nifty400':          'nifty400.csv',
    'nifty_400':         'nifty400.csv',
    'nifty500':          'nifty500.csv',
    'nifty_500':         'nifty500.csv',
    'nifty_smallcap250': 'nifty_smallcap250.csv',
    'nifty_midsmallcap400': 'nifty_midsmallcap400.csv',
}

# Columns tried when reading NSEIndia CSV exports (in priority order)
_SYMBOL_COLS = ['Symbol', 'SYMBOL', 'Ticker', 'TICKER', 'ticker', 'symbol',
                'NSE Symbol', 'NSE_SYMBOL']


class UniverseLoader:
    """
    Resolves a universe specification to a clean list of NSE ticker strings
    (no .NS suffix — DataCollectorAgent adds it internally).

    Usage
    -----
        loader = UniverseLoader(base_dir='.')
        tickers = loader.load('nifty50')          # built-in index
        tickers = loader.load('nifty500')         # via data/universe/nifty500.csv
        tickers = loader.load('/tmp/my_list.csv') # absolute path
        tickers = loader.load('custom', custom=['INFY','TCS'])  # explicit list
    """

    def __init__(self, base_dir: str | Path = '.'):
        self.base_dir   = Path(base_dir)
        self.uni_dir    = self.base_dir / 'data' / 'universe'

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self,
             source: str,
             custom: list[str] | None = None,
             max_size: int | None     = None) -> list[str]:
        """
        Resolve *source* to a ticker list.

        Parameters
        ----------
        source   : 'nifty50' | 'nifty100' | 'nifty500' | '/path/to/file.csv'
                   | 'custom' (uses *custom* kwarg)
        custom   : explicit list override used when source == 'custom'
        max_size : cap the returned list (useful for large indices in testing)

        Returns
        -------
        list[str]  — deduplicated, uppercase, no .NS suffix
        """
        key = source.strip().lower()

        # 1. Explicit list
        if key == 'custom':
            tickers = self._clean(custom or [])
            return tickers[:max_size] if max_size else tickers

        # 2. Built-in index
        if key in BUILT_IN:
            tickers = self._clean(BUILT_IN[key])
            return tickers[:max_size] if max_size else tickers

        # 3. CSV alias → data/universe/{filename}
        if key in CSV_ALIASES:
            fname = CSV_ALIASES[key]
            path  = self.uni_dir / fname
            if path.exists():
                tickers = self._load_csv(path)
                return tickers[:max_size] if max_size else tickers
            raise FileNotFoundError(
                f"Index '{source}' requires '{path}'.\n"
                f"Download from: https://www.nseindia.com/market-data/live-equity-market\n"
                f"  → Select '{source.upper()}' → 'Download complete list' → save as '{fname}'\n"
                f"  → Place file at: {path}"
            )

        # 4. Direct file path (absolute or relative)
        p = Path(source)
        if not p.is_absolute():
            p = self.base_dir / source
        if p.exists() and p.suffix.lower() == '.csv':
            tickers = self._load_csv(p)
            return tickers[:max_size] if max_size else tickers

        # 5. Check data/universe/ for a .csv with that name
        guess = self.uni_dir / f"{source}.csv"
        if guess.exists():
            tickers = self._load_csv(guess)
            return tickers[:max_size] if max_size else tickers

        raise ValueError(
            f"Unknown universe '{source}'.\n"
            f"Built-in options : {sorted(BUILT_IN.keys())}\n"
            f"CSV index aliases: {sorted(CSV_ALIASES.keys())}\n"
            f"Custom CSV file  : pass an absolute or relative path ending in .csv\n"
            f"Explicit list    : pass source='custom' with custom=[...] kwarg"
        )

    def describe(self) -> None:
        """Print available universe options and any downloaded CSVs."""
        print("\n── Built-in universes ──────────────────────────────────────")
        for name, tickers in BUILT_IN.items():
            print(f"  {name:<22} {len(tickers):>4} tickers")

        print("\n── CSV-alias universes (need CSV in data/universe/) ────────")
        for alias, fname in sorted(CSV_ALIASES.items()):
            path   = self.uni_dir / fname
            status = f"✅ {self._csv_count(path)} tickers" if path.exists() else "❌ not downloaded"
            print(f"  {alias:<28} {fname:<35} {status}")

        extra = [f for f in self.uni_dir.glob('*.csv')
                 if f.name not in CSV_ALIASES.values()] if self.uni_dir.exists() else []
        if extra:
            print("\n── Extra CSVs in data/universe/ ────────────────────────────")
            for f in extra:
                print(f"  {f.name:<40} {self._csv_count(f)} tickers")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_csv(self, path: Path) -> list[str]:
        """
        Load tickers from a CSV.  Handles:
          - NSEIndia bulk-download format (Symbol column)
          - Plain single-column files (one ticker per row, no header)
          - Files with BOM or Windows line endings
        """
        raw  = path.read_text(encoding='utf-8-sig').splitlines()
        if not raw:
            return []

        # Try parsing as proper CSV with a header
        reader   = csv.DictReader(raw)
        fieldnames = reader.fieldnames or []

        col = next((c for c in _SYMBOL_COLS if c in fieldnames), None)
        if col:
            return self._clean([row[col] for row in reader if row.get(col)])

        # No recognised header — treat first column as ticker list
        reader2 = csv.reader(raw)
        rows    = list(reader2)
        # Skip header row if it looks like a label (non-alphanumeric first char or contains 'symbol')
        start   = 0
        if rows and rows[0] and rows[0][0].lower() in ('symbol','ticker','name','scrip'):
            start = 1
        return self._clean([r[0] for r in rows[start:] if r and r[0].strip()])

    @staticmethod
    def _clean(tickers: list[str]) -> list[str]:
        """Strip .NS/.BO suffixes, uppercase, deduplicate, drop blanks."""
        seen, out = set(), []
        for t in tickers:
            t = str(t).strip().upper().replace('.NS','').replace('.BO','')
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _csv_count(self, path: Path) -> int:
        try:
            return len(self._load_csv(path))
        except Exception:
            return 0
