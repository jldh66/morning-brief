"""
GARCH(1,1) volatility forecast section for the morning brief.

Walk-forward logic copied from garch_forecast.py:
  - expanding window, refit every 21 days, zero lookahead
  - params estimated on strictly prior data; recursion rolled forward between refits

Position sizing copied from vol_target.py:
  - size = target_vol / forecast_vol, capped [0.25, 2.0]

Returns a box-drawing text block via get_garch_html(), compatible with the existing
email_template._body_to_html() rendering pipeline (╔...╚ blocks become dark HTML tables).
"""

import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── Parameters ─────────────────────────────────────────────────────────────────
_MIN_TRAIN        = 500     # trading days of history before first forecast
_REFIT_EVERY      = 21      # re-estimate GARCH params every N days
_REGIME_LOOKBACK  = 252     # percentile window: 1 trading year (equity)
_PERIODS_PER_YEAR = 252     # equity indices, not crypto
_TARGET_VOL       = 15.0    # annualized target vol %
_BASE_RISK        = 200     # base risk in EUR
_MAX_LEVERAGE     = 2.0
_MIN_SIZE         = 0.25

_INDICES = [
    ('^NDX',  'US100'),
    ('^DJI',  'US30'),
    ('^GSPC', 'US500'),
]

# Dutch action text per regime
_ACTIE = {
    'storm':  '⛈️ STOP — niet traden of kwart positie',
    'normal': '\U0001f324️ NORMAAL — standaard €200 risico',
    'calm':   '☀️ CALM — volle positie, optioneel opschalen',
}

# inner = (5+2)+(8+2)+(6+2)+(7+2)+(5+2)+(5+2)+(44+2) + 6 = 100
_GARCH_COLS = [
    ('INDEX',    5,  'l'),
    ('VOL ANN%', 8,  'r'),
    ('PCTILE',   6,  'r'),
    ('REGIME',   7,  'l'),
    ('SIZE',     5,  'r'),
    ('RISICO',   5,  'r'),
    ('ACTIE',   44,  'l'),
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _cell(val, w, align):
    s = str(val)
    if align == 'r': return ' ' + s.rjust(w)  + ' '
    if align == 'c': return ' ' + s.center(w) + ' '
    return ' ' + s.ljust(w) + ' '


# ── GARCH core (copied from garch_forecast.py) ─────────────────────────────────

def _walkforward_garch(prices: pd.DataFrame) -> pd.DataFrame:
    """
    Walk-forward GARCH(1,1). For each day t >= _MIN_TRAIN, forecasts the vol of
    day t+1 using ONLY data available at close of day t (zero lookahead).

    Params re-estimated every _REFIT_EVERY days on an expanding window.
    Between refits the GARCH recursion is rolled forward with the last fitted
    params — still zero lookahead because params were estimated on prior data only.
    """
    from arch import arch_model

    px = prices["close"].to_numpy(dtype=float)
    rets = 100.0 * np.diff(px) / px[:-1]   # daily % returns, scaled for arch
    n = len(rets)
    if n < _MIN_TRAIN + 10:
        raise ValueError(f"Need ≥{_MIN_TRAIN + 10} observations; got {n + 1}")

    fcast_var = np.full(n, np.nan)
    omega = alpha = beta = mu = None
    sigma2 = None

    for t in range(_MIN_TRAIN, n):
        if (t - _MIN_TRAIN) % _REFIT_EVERY == 0:
            am = arch_model(rets[:t], vol="GARCH", p=1, q=1, mean="Constant", dist="t")
            res = am.fit(disp="off", show_warning=False)
            p = res.params
            mu, omega, alpha, beta = p["mu"], p["omega"], p["alpha[1]"], p["beta[1]"]
            sigma2 = float(res.conditional_volatility[-1] ** 2)
        eps    = rets[t] - mu
        sigma2 = omega + alpha * eps ** 2 + beta * sigma2
        fcast_var[t] = sigma2          # made at close of t, for day t+1

    out = prices.iloc[1:].copy().reset_index(drop=True)
    out["ret"] = rets
    out["fcast_vol"]     = np.sqrt(fcast_var)
    out["fcast_vol_ann"] = out["fcast_vol"] * np.sqrt(_PERIODS_PER_YEAR)
    pct = out["fcast_vol"].rolling(_REGIME_LOOKBACK, min_periods=90).apply(
        lambda w: (w.iloc[:-1] < w.iloc[-1]).mean() * 100 if len(w) > 1 else np.nan,
        raw=False)
    out["vol_pctile"] = pct
    out["regime"] = pd.cut(out["vol_pctile"], bins=[-1, 33, 67, 101],
                           labels=["calm", "normal", "storm"])
    return out


# ── Sizing (copied from vol_target.py) ────────────────────────────────────────

def _size_from_vol(vol_ann: float) -> float:
    """size = target_vol / forecast_vol, capped [0.25, 2.0]."""
    try:
        if vol_ann is None or float(vol_ann) <= 0:
            return _MIN_SIZE
        if np.isnan(float(vol_ann)):
            return _MIN_SIZE
        return float(np.clip(_TARGET_VOL / float(vol_ann), _MIN_SIZE, _MAX_LEVERAGE))
    except Exception:
        return _MIN_SIZE


# ── Per-index forecast ─────────────────────────────────────────────────────────

def _forecast_one(ticker: str) -> dict:
    """Fetch 3yr daily close and run walk-forward GARCH for a single index."""
    import yfinance as yf

    # 3yr (~750 trading days) to satisfy _MIN_TRAIN=500
    data = yf.download(ticker, period="3y", auto_adjust=True, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    prices = data.reset_index()[["Date", "Close"]].copy()
    prices.columns = ["date", "close"]
    prices["date"] = pd.to_datetime(prices["date"])
    prices = prices.dropna().sort_values("date").reset_index(drop=True)
    prices = prices[prices["close"] > 0]

    wf = _walkforward_garch(prices)
    latest = wf.dropna(subset=["fcast_vol"]).iloc[-1]

    vol_ann = float(latest["fcast_vol_ann"])
    pctile  = float(latest["vol_pctile"]) if pd.notna(latest["vol_pctile"]) else None
    regime  = str(latest["regime"])
    mult    = _size_from_vol(vol_ann)

    return {
        "vol_ann": round(vol_ann, 1),
        "pctile":  round(pctile, 1) if pctile is not None else None,
        "regime":  regime,
        "mult":    mult,
        "dollar":  int(round(mult * _BASE_RISK)),
    }


# ── Public section builder ─────────────────────────────────────────────────────

def get_garch_html() -> str:
    """
    Returns a box-drawing text block for the morning brief sections list.
    email_template._body_to_html() converts ╔...╚ blocks into dark HTML tables
    with #4FC3F7 headers — matching the existing template automatically.

    Catches per-index failures: any index that errors shows FOUT, others still display.
    If all indices fail, returns a compact fallback block ("GARCH niet beschikbaar").
    """
    cols  = _GARCH_COLS
    n_col = len(cols)
    inner = sum(w + 2 for _, w, _ in cols) + (n_col - 1)
    pad   = lambda t: '║ ' + t.ljust(inner - 1) + '║'
    hdiv  = lambda l, m, r: l + m.join('═' * (w + 2) for _, w, _ in cols) + r
    rdiv  = '╟' + '╫'.join('─' * (w + 2) for _, w, _ in cols) + '╢'
    hrow  = lambda vals: '║' + '║'.join(_cell(v, w, a) for (_, w, a), v in zip(cols, vals)) + '║'

    # Collect results — catch per-index failures
    results = []
    for ticker, label in _INDICES:
        try:
            r = _forecast_one(ticker)
            results.append((label, r, None))
        except Exception as ex:
            results.append((label, None, str(ex)[:50]))

    # All failed → compact fallback
    if all(r is None for _, r, _ in results):
        return '\n'.join([
            '╔' + '═' * inner + '╗',
            pad('  ⚡ STORM GAUGE  —  GARCH Volatiliteitsvoorspelling'),
            pad('  GARCH niet beschikbaar — check logs'),
            '╚' + '═' * inner + '╝',
        ])

    lines = [
        '╔' + '═' * inner + '╗',
        pad('  ⚡ STORM GAUGE  —  GARCH(1,1) Volatiliteitsvoorspelling  |  252-dag walk-forward'),
        hdiv('╠', '╦', '╣'),
        hrow(name.center(w) for name, w, _ in cols),
        hdiv('╠', '╬', '╣'),
    ]

    for i, (label, r, err) in enumerate(results):
        if err is not None:
            row = [label, 'FOUT', '-', '-', '-', '-', (err[:44])]
        else:
            pctile_str = f"{r['pctile']:.1f}" if r['pctile'] is not None else 'N/A'
            actie      = _ACTIE.get(r['regime'], r['regime'].upper())
            row = [
                label,
                f"{r['vol_ann']}%",
                pctile_str,
                r['regime'].upper(),
                f"{r['mult']:.2f}×",   # ×
                f"€{r['dollar']}",      # €
                actie,
            ]
        lines.append(hrow(row))
        if i < len(results) - 1:
            lines.append(rdiv)

    lines += [
        hdiv('╠', '╩', '╣'),
        pad('  GARCH voorspelt marktgeweld, niet richting. Entries komen van je indicators.'),
        '╚' + '═' * inner + '╝',
    ]
    return '\n'.join(lines)


if __name__ == '__main__':
    print(get_garch_html())
