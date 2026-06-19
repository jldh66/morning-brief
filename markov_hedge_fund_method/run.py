"""CLI entry point: fetch -> label -> matrix -> stationary -> walk-forward.

Gebruik:
    uv run python -m markov_hedge_fund_method.run --ticker SPY --years 10 --window 20
    uv run python -m markov_hedge_fund_method.run --ticker AAPL --years 5
    uv run python -m markov_hedge_fund_method.run --ticker BTC-USD --years 3 --no-hmm
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .regime import (
    STATES,
    label_regimes,
    build_transition_matrix,
    stationary_distribution,
    walk_forward_backtest,
)

HMM_FLAG_FILE = Path(__file__).resolve().parent.parent / ".hmm_available"


def _hmm_available() -> bool:
    if HMM_FLAG_FILE.exists():
        return HMM_FLAG_FILE.read_text().strip().lower() == "true"
    try:
        import hmmlearn  # noqa: F401
        return True
    except ImportError:
        return False


def _fetch_with_retry(ticker: str, years: int) -> pd.DataFrame:
    """Haal data op via yfinance met één retry bij fout."""
    import yfinance as yf

    end = pd.Timestamp.utcnow().normalize()
    start = end - pd.DateOffset(years=years)

    for attempt in (1, 2):
        try:
            df = yf.download(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                progress=False,
                auto_adjust=True,
            )
        except Exception as exc:
            print(f"  ! yfinance fout bij poging {attempt}: {exc}")
            df = pd.DataFrame()

        if not df.empty:
            return df

        if attempt == 1:
            print("  ! yfinance gaf lege data terug — opnieuw proberen over 30s.")
            time.sleep(30)

    raise RuntimeError(
        f"yfinance gaf lege data voor {ticker} na herpoging. "
        "Yahoo kan rate-limitten. Probeer over een paar minuten opnieuw."
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="markov-hedge-fund-method")
    parser.add_argument("--ticker", default="SPY", help="Ticker symbool (bijv. SPY, AAPL, BTC-USD)")
    parser.add_argument("--years", type=int, default=10, help="Jaren historische data")
    parser.add_argument("--window", type=int, default=20, help="Rollend venster in handelsdagen")
    parser.add_argument("--threshold", type=float, default=0.02, help="Drempel voor regime-labeling")
    parser.add_argument("--no-hmm", action="store_true", help="Sla HMM over")
    args = parser.parse_args()

    print(f"\nmarkov-hedge-fund-method — ticker={args.ticker} jaren={args.years} venster={args.window}")
    print(f"  Ophalen {args.ticker} van Yahoo Finance...")
    df = _fetch_with_retry(args.ticker, args.years)

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    close = df["Close"].dropna()
    print(f"  {len(close)} rijen opgehaald | {close.index.min().date()} → {close.index.max().date()}")

    labels = label_regimes(close, window=args.window, threshold=args.threshold)
    P = build_transition_matrix(labels)
    pi = stationary_distribution(P)

    print("\nTransitiematrix (rijen = van, kolommen = naar):")
    print(f"            {STATES[0]:>9s} {STATES[1]:>9s} {STATES[2]:>9s}")
    for i, from_state in enumerate(STATES):
        row = "  ".join(f"{P[i, j]*100:7.2f}%" for j in range(3))
        print(f"  {from_state:>9s}  {row}")

    print("\nPersistentie diagonaal:")
    for i, s in enumerate(STATES):
        print(f"  {s} → {s}: {P[i,i]*100:.2f}%")

    print("\nStationaire verdeling (lange-termijn regimemix):")
    for s, p in zip(STATES, pi):
        print(f"  {s:>9s}: {p*100:.2f}%")

    print("\nWalk-forward backtest (matrix elke stap opnieuw geschat, geen lookahead)...")
    result = walk_forward_backtest(close, labels)
    sharpe = result["sharpe"]
    mdd = result["max_drawdown"]
    if np.isfinite(sharpe):
        print(f"  Sharpe (geannualiseerd, walk-forward): {sharpe:.3f}")
    else:
        print("  Sharpe: NaN (onvoldoende data — probeer langere periode of andere ticker)")
    if np.isfinite(mdd):
        print(f"  Max drawdown:                           {mdd*100:.2f}%")
    else:
        print("  Max drawdown: NaN")
    print(f"  Trades geëvalueerd: {result['n_trades']}")

    if not args.no_hmm and _hmm_available():
        print("\nHidden Markov Model fitten (Baum-Welch + Viterbi via hmmlearn)...")
        try:
            from .hmm_extension import fit_hmm
            returns = close.pct_change().dropna()
            model, hidden = fit_hmm(returns, n_components=3)
            if model is None:
                print("  HMM overgeslagen (hmmlearn import mislukt).")
            else:
                means = np.array([model.means_[k][0] for k in range(model.n_components)])
                order = np.argsort(means)
                labels_hmm = ["Bear (laagste gem. rendement)", "Sideways", "Bull (hoogste gem. rendement)"]
                print("  HMM regime gemiddelde dagrendementen (gesorteerd):")
                for rank, k in enumerate(order):
                    print(f"    {labels_hmm[rank]:<34s} staat {k}: {means[k]*100:+.3f}% per dag")
                print("  Let op: Baum-Welch vindt lokale maxima. Voor productie: meerdere random_state waarden proberen.")
        except Exception as exc:
            print(f"  HMM overgeslagen: {exc}")
    else:
        print("\nHMM overgeslagen (optioneel). Observable Markov model succesvol uitgevoerd.")

    print("\n================================================================")
    print(f" Framework: Roan (@RohOnChain)")
    print(f" Backtests zijn historisch — geen voorspelling van toekomstig rendement.")
    print("================================================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
