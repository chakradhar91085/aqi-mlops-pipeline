import numpy as np
import pandas as pd

from ml.features import FEATURES, build_feature_frame, chronological_split, make_xy


def _daily(n=30, drop=None):
    dates = pd.date_range("2026-01-01", periods=n)
    df = pd.DataFrame({"city": "Delhi", "date": dates, "aqi": np.arange(100, 100 + n, dtype=float)})
    if drop is not None:
        df = df.drop(index=drop)
    return df


def test_lags_and_targets_align_with_calendar_days():
    f = build_feature_frame(_daily()).set_index("date")
    t = pd.Timestamp("2026-01-15")
    assert f.loc[t, "aqi_lag1"] == f.loc[t - pd.Timedelta(days=1), "aqi"]
    assert f.loc[t, "aqi_lag7"] == f.loc[t - pd.Timedelta(days=7), "aqi"]
    assert f.loc[t, "y_aqi"] == f.loc[t + pd.Timedelta(days=1), "aqi"]


def test_missing_day_gives_nan_not_shifted_value():
    f = build_feature_frame(_daily(drop=[9])).set_index("date")  # drops 2026-01-10
    assert np.isnan(f.loc["2026-01-11", "aqi_lag1"])


def test_rolling_features_do_not_see_the_future():
    f = build_feature_frame(_daily()).set_index("date")
    t = pd.Timestamp("2026-01-15")
    assert f.loc[t, "aqi_roll3_mean"] == (112 + 113 + 114) / 3  # t-2..t only


def test_no_feature_equals_target():
    f = build_feature_frame(_daily())
    X, y, _ = make_xy(f)
    for c in FEATURES:
        if c != "city":
            assert not np.allclose(X[c].astype(float), y.astype(float)), c


def test_chronological_split_has_no_overlap():
    f = build_feature_frame(_daily(60))
    tr, va, te, _ = chronological_split(f)
    assert tr["date"].max() < va["date"].min() <= va["date"].max() < te["date"].min()
