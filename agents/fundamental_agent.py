# SwingTradeIQ — Agent 4/10: FundamentalAgent
# Built in Session 4.
# Handoff: reads data/meta/universe_meta.json + data/eda/eda_summary.json
#          → writes data/fundamental/

import time, json
from pathlib import Path
from datetime import datetime

import yaml

# ── Scoring bands ─────────────────────────────────────────────────────────────
# Each band list is [(upper_bound_exclusive, score), ...] read top-to-bottom.
# The last entry acts as the floor (no upper bound check needed).

_PE_BANDS = [
    (10,  10.0),
    (15,   9.0),
    (20,   7.0),
    (25,   5.0),
    (30,   3.0),
    (None, 1.0),
]

_PE_BANDS_BANK = [          # banks carry structural leverage → lower PE is common
    (12,  10.0),
    (18,   8.0),
    (25,   5.0),
    (None, 2.0),
]

_PB_BANDS = [
    (1,    10.0),
    (2,     9.0),
    (3,     7.0),
    (5,     5.0),
    (8,     3.0),
    (None,  1.0),
]

_PB_BANDS_BANK = [
    (1.5,  10.0),
    (2.0,   9.0),
    (3.0,   7.0),
    (4.0,   4.0),
    (None,  1.0),
]

_ROE_BANDS = [              # higher is better; values are fractions (0.28 = 28%)
    (None, 10.0),           # sentinel: handled by _score_roe directly
]

_DE_BANDS = [               # lower is better
    (10,   10.0),
    (20,    8.0),
    (30,    6.0),
    (50,    4.0),
    (70,    2.0),
    (None,  0.0),
]

_GROSS_MARGIN_BANDS = [     # higher is better; fraction
    (0.40, 10.0),
    (0.30,  8.0),
    (0.20,  6.0),
    (0.10,  4.0),
    (None,  2.0),
]

_CURRENT_RATIO_BANDS = [
    (2.0,  10.0),
    (1.5,   8.0),
    (1.0,   6.0),
    (0.5,   2.0),
    (None,  0.0),
]

def _score_div_yield(y: float) -> float:
    """Sweet-spot scoring: 4–6 % is ideal; > 6 % flagged as possible distress."""
    if y >= 0.06: return 6.0
    if y >= 0.04: return 10.0
    if y >= 0.02: return 9.0
    if y >= 0.01: return 7.0
    return 5.0

# ── Metric weights by sector group ────────────────────────────────────────────
# Weights sum to 1.0. Metrics not in the dict are skipped for that group.
_WEIGHTS_STANDARD = {
    'pe':            0.20,
    'pb':            0.15,
    'roe':           0.20,
    'de':            0.15,
    'revenue_growth':0.15,
    'gross_margin':  0.10,
    'current_ratio': 0.05,
}

_WEIGHTS_FINANCIAL = {      # banks: skip D/E, gross margin, current ratio
    'pe':            0.25,
    'pb':            0.20,
    'roe':           0.30,
    'revenue_growth':0.20,
    'div_yield':     0.05,
}

FINANCIAL_SECTORS = {'Financial Services', 'Banking', 'Insurance'}


def _band_score(value: float, bands: list) -> float:
    """Lower-is-better. Each entry: (upper_bound_exclusive, score). Walk top-to-bottom."""
    for upper, score in bands:
        if upper is None or value < upper:
            return score
    return bands[-1][1]


def _band_score_ge(value: float, bands: list) -> float:
    """Higher-is-better. Each entry: (lower_bound, score), listed highest-first."""
    for lower, score in bands:
        if lower is None or value >= lower:
            return score
    return bands[-1][1]


def _score_roe(roe: float) -> float:
    if roe >= 0.30: return 10.0
    if roe >= 0.20: return 8.0
    if roe >= 0.15: return 6.0
    if roe >= 0.10: return 4.0
    if roe >= 0.05: return 2.0
    return 0.0


def _score_revenue_growth(g: float) -> float:
    if g >= 0.20: return 10.0
    if g >= 0.10: return 8.0
    if g >= 0.05: return 6.0
    if g >= 0.00: return 4.0
    # negative: linear decay, floor at 0
    return max(0.0, round(4.0 + g * 20, 2))


class FundamentalAgent:
    """
    Agent 4/10 — SwingTradeIQ Fundamental Analysis Agent.

    Scores each stock on 6-8 fundamental metrics with sector-aware weighting,
    produces a composite score on a 0–10 scale, and applies the
    min_fundamental_score filter from config.yaml to produce a pass/fail
    decision for downstream agents.

    Scoring approach
    ----------------
    Standard sectors (non-financial):
        PE (20%) · PB (15%) · ROE (20%) · D/E (15%) ·
        Revenue Growth (15%) · Gross Margin (10%) · Current Ratio (5%)

    Financial Services / Banking:
        PE (25%) · PB (20%) · ROE (30%) · Revenue Growth (20%) ·
        Dividend Yield (5%)
        — D/E, gross margin, current ratio skipped (structurally inapplicable)

    Missing values:
        Metric is excluded from the weighted average; remaining weights
        are re-normalised so the score always uses the full 0–10 scale.

    Inputs
    ------
        data/meta/universe_meta.json   — fundamentals from DataCollectorAgent
        data/eda/eda_summary.json      — return / risk context from EDAAgent
        config.yaml                    — min_fundamental_score threshold

    Outputs
    -------
        data/fundamental/{TICKER}_fundamental.json  — full per-ticker profile
        data/fundamental/fundamental_summary.json   — ranking + pass/fail list
        logs/fundamental_{DATE}.txt                 — run log
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir       = Path(base_dir)
        self.meta_path      = self.base_dir / 'data' / 'meta' / 'universe_meta.json'
        self.eda_summary    = self.base_dir / 'data' / 'eda'  / 'eda_summary.json'
        self.fundamental_dir= self.base_dir / 'data' / 'fundamental'
        self.log_dir        = self.base_dir / 'logs'
        self.config_path    = self.base_dir / 'config.yaml'

        for d in [self.fundamental_dir, self.log_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._log_lines: list[str] = []

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self._log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _load_config(self) -> dict:
        with open(self.config_path) as f:
            return yaml.safe_load(f)

    def _load_meta(self) -> list[dict]:
        with open(self.meta_path) as f:
            return json.load(f)

    def _load_eda_context(self) -> dict:
        """Return a dict keyed by ticker_clean for quick lookup."""
        if not self.eda_summary.exists():
            return {}
        with open(self.eda_summary) as f:
            summary = json.load(f)
        return {row['ticker']: row for row in summary.get('per_ticker', [])}

    def _r(self, v, d: int = 4):
        if v is None: return None
        try: return round(float(v), d)
        except (TypeError, ValueError): return None

    # ──────────────────────────────────────────────────────────────────────────
    # Scoring
    # ──────────────────────────────────────────────────────────────────────────

    def _score_ticker(self, meta: dict) -> dict:
        sector    = meta.get('sector') or ''
        is_bank   = sector in FINANCIAL_SECTORS
        weights   = _WEIGHTS_FINANCIAL if is_bank else _WEIGHTS_STANDARD
        pe_bands  = _PE_BANDS_BANK     if is_bank else _PE_BANDS
        pb_bands  = _PB_BANDS_BANK     if is_bank else _PB_BANDS

        raw = {
            'pe':            meta.get('trailingPE'),
            'pb':            meta.get('priceToBook'),
            'roe':           meta.get('returnOnEquity'),
            'de':            meta.get('debtToEquity'),
            'revenue_growth':meta.get('revenueGrowth'),
            'gross_margin':  meta.get('grossMargins'),
            'current_ratio': meta.get('currentRatio'),
            'div_yield':     meta.get('dividendYield'),
        }

        # Convert dividendYield: yfinance returns percent (e.g. 4.8) not fraction
        if raw['div_yield'] is not None:
            raw['div_yield'] = raw['div_yield'] / 100.0

        scored: dict[str, dict] = {}

        def _add(key, value, bands=None, fn=None):
            if key not in weights or value is None:
                return
            score = fn(value) if fn else _band_score(value, bands)
            scored[key] = {
                'value': self._r(value, 4),
                'score': round(score, 2),
                'weight': weights[key],
            }

        _add('pe',             raw['pe'],             pe_bands)
        _add('pb',             raw['pb'],             pb_bands)
        _add('roe',            raw['roe'],             fn=_score_roe)
        _add('de',             raw['de'],             _DE_BANDS)
        _add('revenue_growth', raw['revenue_growth'],  fn=_score_revenue_growth)
        # gross_margin and current_ratio: higher is better → use _band_score_ge
        _add('gross_margin',   raw['gross_margin'],   _GROSS_MARGIN_BANDS,  fn=lambda v: _band_score_ge(v, _GROSS_MARGIN_BANDS))
        _add('current_ratio',  raw['current_ratio'],  _CURRENT_RATIO_BANDS, fn=lambda v: _band_score_ge(v, _CURRENT_RATIO_BANDS))
        _add('div_yield',      raw['div_yield'],      None,                 fn=_score_div_yield)

        # Weighted average with re-normalisation for missing metrics
        total_weight = sum(v['weight'] for v in scored.values())
        if total_weight == 0:
            composite = 0.0
        else:
            composite = sum(v['score'] * v['weight'] for v in scored.values()) / total_weight
            composite = round(composite, 2)

        return {'metric_scores': scored, 'composite_score': composite}

    # ──────────────────────────────────────────────────────────────────────────
    # Per-ticker profile
    # ──────────────────────────────────────────────────────────────────────────

    def _analyse_one(self, meta: dict, eda_ctx: dict, threshold: float) -> dict:
        ticker = meta.get('ticker_clean') or meta['ticker'].replace('.NS','').replace('.BO','')

        scoring  = self._score_ticker(meta)
        score    = scoring['composite_score']
        passed   = score >= threshold

        # Market cap in Cr (1 Cr = 10M)
        mc_raw = meta.get('marketCap')
        mc_cr  = round(mc_raw / 1e7, 0) if mc_raw else None

        profile = {
            'ticker':        ticker,
            'short_name':    meta.get('shortName'),
            'sector':        meta.get('sector'),
            'industry':      meta.get('industry'),
            'currency':      meta.get('currency', 'INR'),
            'market_cap_cr': mc_cr,
            'fundamental_score': score,
            'pass':          passed,
            'threshold':     threshold,
            'metric_scores': scoring['metric_scores'],
            'raw_fundamentals': {
                'pe':             self._r(meta.get('trailingPE'), 2),
                'pb':             self._r(meta.get('priceToBook'), 2),
                'roe_pct':        self._r((meta.get('returnOnEquity') or 0) * 100, 1),
                'de':             self._r(meta.get('debtToEquity'), 2),
                'revenue_growth_pct': self._r((meta.get('revenueGrowth') or 0) * 100, 1),
                'gross_margin_pct': self._r((meta.get('grossMargins') or 0) * 100, 1),
                'current_ratio':  self._r(meta.get('currentRatio'), 2),
                'beta':           self._r(meta.get('beta'), 3),
                'dividend_yield_pct': self._r(meta.get('dividendYield'), 2),
                'operating_cashflow_cr': round(meta['operatingCashflow'] / 1e7, 1) if meta.get('operatingCashflow') else None,
            },
            'eda_context': eda_ctx.get(ticker, {}),
        }

        out_path = self.fundamental_dir / f"{ticker}_fundamental.json"
        with open(out_path, 'w') as f:
            json.dump(profile, f, indent=2, default=str)

        return profile

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        start_time      = time.time()
        self._log_lines = []

        cfg       = self._load_config()
        threshold = cfg['filters']['min_fundamental_score']
        meta_list = self._load_meta()
        eda_ctx   = self._load_eda_context()

        self._log(f"FundamentalAgent — {len(meta_list)} tickers, threshold={threshold}")
        print(f"{'='*60}")
        print(f"FundamentalAgent.run() — {len(meta_list)} tickers | threshold={threshold}")
        print(f"{'='*60}")

        profiles  = []
        succeeded = []
        failed    = []
        out_paths = []

        for i, meta in enumerate(meta_list, 1):
            ticker = meta.get('ticker_clean') or meta['ticker'].replace('.NS','').replace('.BO','')
            print(f"[{i:>3}/{len(meta_list)}] {ticker:<16}", end='', flush=True)

            if meta.get('error'):
                reason = meta['error']
                failed.append({'ticker': ticker, 'reason': reason})
                print(f"  ❌ meta fetch error: {reason}")
                continue

            try:
                profile = self._analyse_one(meta, eda_ctx, threshold)
                profiles.append(profile)
                succeeded.append(ticker)
                out_paths.append(str(self.fundamental_dir / f"{ticker}_fundamental.json"))

                ms    = profile['metric_scores']
                label = '✅ PASS' if profile['pass'] else '⚠ FAIL'
                print(
                    f"  {label}  score={profile['fundamental_score']:.1f}/10"
                    f"  PE={profile['raw_fundamentals']['pe']}"
                    f"  ROE={profile['raw_fundamentals']['roe_pct']}%"
                    f"  D/E={profile['raw_fundamentals']['de']}"
                )
                self._log(
                    f"{ticker}: score={profile['fundamental_score']} pass={profile['pass']}"
                )
            except Exception as exc:
                failed.append({'ticker': ticker, 'reason': str(exc)})
                print(f"  ❌ {exc}")
                self._log(f"ERROR {ticker}: {exc}")

        # ── Summary + ranking ─────────────────────────────────────────────────
        ranking = sorted(profiles, key=lambda p: p['fundamental_score'], reverse=True)

        summary = {
            'run_at':    datetime.now().isoformat(),
            'threshold': threshold,
            'total':     len(meta_list),
            'passed':    [p['ticker'] for p in profiles if p['pass']],
            'failed_filter': [p['ticker'] for p in profiles if not p['pass']],
            'errored':   failed,
            'ranking': [
                {
                    'rank':               i + 1,
                    'ticker':             p['ticker'],
                    'sector':             p['sector'],
                    'fundamental_score':  p['fundamental_score'],
                    'pass':               p['pass'],
                    'pe':                 p['raw_fundamentals']['pe'],
                    'pb':                 p['raw_fundamentals']['pb'],
                    'roe_pct':            p['raw_fundamentals']['roe_pct'],
                    'de':                 p['raw_fundamentals']['de'],
                    'revenue_growth_pct': p['raw_fundamentals']['revenue_growth_pct'],
                    'market_cap_cr':      p['market_cap_cr'],
                }
                for i, p in enumerate(ranking)
            ],
        }

        summary_path = self.fundamental_dir / 'fundamental_summary.json'
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        out_paths.append(str(summary_path))

        # ── Console ranking table ─────────────────────────────────────────────
        elapsed = time.time() - start_time
        print(f"\n{'─'*60}")
        print(f"{'Rank':<5} {'Ticker':<14} {'Score':>5}  {'PE':>6}  {'ROE%':>6}  {'RevG%':>6}  Sector")
        print(f"{'─'*60}")
        for row in summary['ranking']:
            verdict = '✅' if row['pass'] else '⚠ '
            print(
                f"{row['rank']:<5} {row['ticker']:<14} "
                f"{verdict}{row['fundamental_score']:>4.1f}  "
                f"{str(row['pe'] or 'N/A'):>6}  "
                f"{str(row['roe_pct'] or 'N/A'):>6}  "
                f"{str(row['revenue_growth_pct'] or 'N/A'):>6}  "
                f"{row['sector']}"
            )

        # ── Log ───────────────────────────────────────────────────────────────
        log_path = self.log_dir / f"fundamental_{datetime.now().strftime('%Y-%m-%d')}.txt"
        self._log(f"Done — {len(summary['passed'])} pass, {len(summary['failed_filter'])} fail filter, {elapsed:.1f}s")
        with open(log_path, 'w') as f:
            f.write('\n'.join([
                'SwingTradeIQ FundamentalAgent',
                f"Date     : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"Duration : {elapsed:.1f}s",
                f"Threshold: {threshold}",
                '',
                *self._log_lines,
            ]))

        print(f"\n{'='*60}")
        print(f"Done in {elapsed:.1f}s | "
              f"✅ {len(summary['passed'])} passed | "
              f"⚠  {len(summary['failed_filter'])} below threshold | "
              f"❌ {len(failed)} errored")
        print(f"{'='*60}")

        return {
            'succeeded':    succeeded,
            'failed':       failed,
            'output_paths': out_paths,
            'elapsed_s':    round(elapsed, 1),
            'passed':       summary['passed'],
            'ranking':      summary['ranking'],
        }
