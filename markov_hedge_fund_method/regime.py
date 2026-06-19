"""Observable Markov regime model.

Labels elke dag als Bull (2), Bear (0) of Sideways (1) via een rollend rendement,
bouwt een 3x3 transitiematrix via MLE, lost de stationaire verdeling op,
en voert een walk-forward backtest uit zonder lookahead.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STATES = ["Bear", "Sideways", "Bull"]  # index 0, 1, 2


def label_regimes(close: pd.Series, window: int = 20, threshold: float = 0.02) -> pd.Series:
    """Label elke dag als Bull / Bear / Sideways op basis van rollend rendement."""
    rolling_return = close.pct_change(window)
    labels = pd.Series(1, index=close.index, dtype=int)  # standaard Sideways
    labels[rolling_return > threshold] = 2   # Bull
    labels[rolling_return < -threshold] = 0  # Bear
    return labels.dropna()


def build_transition_matrix(labels: pd.Series) -> np.ndarray:
    """MLE schatting van de 3x3 transitiematrix."""
    n = 3
    counts = np.zeros((n, n), dtype=float)
    arr = labels.to_numpy()
    for i in range(len(arr) - 1):
        counts[arr[i], arr[i + 1]] += 1
    row_sums = counts.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    return counts / row_sums


def stationary_distribution(P: np.ndarray) -> np.ndarray:
    """Linker eigenvector van P met eigenwaarde 1, genormaliseerd op som=1."""
    eigvals, eigvecs = np.linalg.eig(P.T)
    idx = np.argmin(np.abs(eigvals - 1.0))
    vec = np.real(eigvecs[:, idx])
    vec = np.abs(vec)
    return vec / vec.sum()


def n_step_forecast(P: np.ndarray, n: int) -> np.ndarray:
    """Chapman-Kolmogorov: P^n is de n-stap transitiematrix."""
    return np.linalg.matrix_power(P, n)


def signal_from_matrix(P: np.ndarray, current_state: int) -> float:
    """Signaal: P(volgend=Bull|huidig) - P(volgend=Bear|huidig)."""
    return float(P[current_state, 2] - P[current_state, 0])


def walk_forward_backtest(
    close: pd.Series,
    labels: pd.Series,
    min_train: int = 252,
) -> dict:
    """Walk-forward backtest zonder lookahead. Matrix wordt elke stap opnieuw geschat."""
    daily_returns = close.pct_change().dropna()
    common_index = labels.index.intersection(daily_returns.index)
    labels = labels.loc[common_index]
    daily_returns = daily_returns.loc[common_index]

    if len(labels) < min_train + 30:
        return {"sharpe": float("nan"), "max_drawdown": float("nan"), "n_trades": 0}

    strategy_returns = []
    for t in range(min_train, len(labels) - 1):
        P_t = build_transition_matrix(labels.iloc[:t])
        current_state = int(labels.iloc[t])
        signal = signal_from_matrix(P_t, current_state)
        position = float(np.sign(signal))
        next_day_return = float(daily_returns.iloc[t + 1])
        strategy_returns.append(position * next_day_return)

    sr = np.array(strategy_returns, dtype=float)
    std = sr.std(ddof=1)
    sharpe = float(sr.mean() / std * np.sqrt(252)) if std != 0 and np.isfinite(std) else float("nan")

    equity = (1.0 + sr).cumprod()
    running_max = np.maximum.accumulate(equity)
    drawdown = (equity - running_max) / running_max
    max_dd = float(drawdown.min()) if len(drawdown) else float("nan")

    return {"sharpe": sharpe, "max_drawdown": max_dd, "n_trades": int(len(sr))}
