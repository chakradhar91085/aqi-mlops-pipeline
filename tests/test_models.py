import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from ml.models import DeltaRegressor


def test_delta_regressor_predicts_today_plus_learned_change():
    aqi = np.arange(100, 140, dtype=float)
    X = pd.DataFrame({"aqi": aqi})
    y = aqi + 5.0  # tomorrow is always today + 5
    m = DeltaRegressor(LinearRegression()).fit(X, y)
    assert np.allclose(m.predict(X), y)


def test_delta_regressor_with_unfit_model_defaults_to_persistence():
    X = pd.DataFrame({"aqi": [50.0, 80.0, 120.0, 90.0]})
    m = DeltaRegressor(LinearRegression()).fit(X, X["aqi"])  # zero change everywhere
    assert np.allclose(m.predict(X), X["aqi"])
