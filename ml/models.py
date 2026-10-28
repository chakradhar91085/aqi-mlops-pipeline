"""Custom estimators. Kept in their own module so pickled models load by a stable import path."""

from __future__ import annotations

import numpy as np
from sklearn.base import BaseEstimator, RegressorMixin, clone


class DeltaRegressor(RegressorMixin, BaseEstimator):
    """Predict tomorrow's AQI as today's `aqi` + a learned change (starts from the persistence forecast)."""

    def __init__(self, estimator):
        self.estimator = estimator

    def fit(self, X, y):
        self.estimator_ = clone(self.estimator).fit(X, np.asarray(y) - X["aqi"].to_numpy())
        return self

    def predict(self, X):
        return self.estimator_.predict(X) + X["aqi"].to_numpy()
