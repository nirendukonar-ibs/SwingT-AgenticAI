# Stock Universe CSV Files

Place NSE index CSV files here to use larger universes.
Nifty 50 and Nifty 100 are bundled in UniverseLoader — no CSV needed for those.

## How to download from NSEIndia

1. Go to https://www.nseindia.com/market-data/live-equity-market
2. Select the index from the dropdown (e.g. "NIFTY 500")
3. Click "Download (.csv)" at the top-right of the table
4. Save the file here with the exact name listed below

## Expected filenames

| Universe flag          | Filename to save here        | Approx size |
|------------------------|------------------------------|-------------|
| `--universe nifty200`  | `nifty200.csv`               | ~200 rows   |
| `--universe nifty500`  | `nifty500.csv`               | ~500 rows   |
| `--universe nifty_midcap150` | `nifty_midcap150.csv`  | ~150 rows   |
| `--universe nifty_smallcap250` | `nifty_smallcap250.csv` | ~250 rows |
| `--universe nifty250`  | `nifty250.csv`               | ~250 rows   |
| `--universe nifty300`  | `nifty300.csv`               | ~300 rows   |
| `--universe nifty350`  | `nifty350.csv`               | ~350 rows   |
| `--universe nifty400`  | `nifty400.csv`               | ~400 rows   |

## Custom CSV format

Any CSV with a `Symbol` column works:

```
Symbol,Company Name,Series,ISIN Code
INFY,Infosys Limited,EQ,INE009A01021
TCS,Tata Consultancy Services,EQ,INE467B01029
```

Or a plain single-column file (no header needed):
```
INFY
TCS
RELIANCE
```

Run `python swingtrade_iq.py --csv data/universe/my_picks.csv` to use it.

## Performance note

Processing 500 stocks takes 30–60 minutes for a full scan (mostly network I/O).
Set `max_universe_size` in config.yaml to cap this during testing.
For daily scans, Nifty 50 or Nifty 100 is recommended.
Use Nifty 500 for weekly reviews or backtest universe expansion studies.
