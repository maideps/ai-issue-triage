"""Logistic-regression weights as plain JSON (D-016).

Trained models are saved as their coefficients, not as pickles: a JSON file can be
committed and reviewed, loading it executes no code, and it survives scikit-learn upgrades.
"""

import json
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression


def new_model(c: float = 1.0) -> LogisticRegression:
    return LogisticRegression(C=c, max_iter=2000, class_weight="balanced")


def to_json(model: LogisticRegression) -> str:
    return json.dumps(
        {
            "classes": model.classes_.tolist(),
            "coef": model.coef_.tolist(),
            "intercept": model.intercept_.tolist(),
        }
    )


def from_json(text: str) -> LogisticRegression:
    data: dict[str, Any] = json.loads(text)
    model = new_model()
    model.classes_ = np.asarray(data["classes"])
    model.coef_ = np.asarray(data["coef"], dtype=np.float64)
    model.intercept_ = np.asarray(data["intercept"], dtype=np.float64)
    model.n_features_in_ = model.coef_.shape[1]
    return model
