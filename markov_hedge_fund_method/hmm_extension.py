"""Optionele Hidden Markov Model laag. Importeert hmmlearn lui zodat
het observable model nog steeds werkt als hmmlearn niet geïnstalleerd is."""

from __future__ import annotations

import numpy as np
import pandas as pd


def fit_hmm(returns: pd.Series, n_components: int = 3, random_state: int = 42):
    """Pas een Gaussisch HMM aan op dagrendementen. Geeft (model, hidden_states) terug."""
    try:
        from hmmlearn import hmm
    except ImportError:
        return None, None

    X = returns.dropna().to_numpy().reshape(-1, 1)
    model = hmm.GaussianHMM(
        n_components=n_components,
        covariance_type="diag",
        n_iter=200,
        random_state=random_state,
    )
    model.fit(X)
    hidden_states = model.predict(X)
    return model, hidden_states
