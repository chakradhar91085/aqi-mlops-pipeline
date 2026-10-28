import numpy as np

from ml.monitoring import evaluate_retrain, psi


def test_psi_identical_samples_is_near_zero():
    x = np.random.default_rng(0).normal(100, 20, 500)
    assert psi(x, x) < 0.01


def test_psi_flags_a_shifted_distribution():
    rng = np.random.default_rng(1)
    assert psi(rng.normal(100, 20, 500), rng.normal(140, 20, 200)) > 0.25


def test_psi_ignores_nans_and_handles_tiny_samples():
    assert np.isnan(psi([1.0], [2.0]))
    assert psi([1, 2, 3, np.nan, 5, 6, 7, 8], [1, 2, 3, 5, 6, 7, 8, np.nan]) < 0.1


def test_retrain_not_triggered_when_healthy():
    assert evaluate_retrain(40.0, 20, 38.0, 0, 5) == []


def test_retrain_triggers_each_criterion():
    assert any("performance" in r for r in evaluate_retrain(60.0, 20, 38.0, 0, 5))
    assert any("drift" in r for r in evaluate_retrain(None, 0, 38.0, 2, 5))
    assert any("new data" in r for r in evaluate_retrain(None, 0, 38.0, 0, 30))


def test_mae_trigger_needs_enough_live_points():
    assert evaluate_retrain(500.0, 3, 38.0, 0, 5) == []


def test_seasonal_reference_keeps_same_time_of_year():
    import pandas as pd

    from ml.monitoring import seasonal_reference

    dates = pd.date_range("2025-10-01", "2026-06-14")
    ref = pd.DataFrame({"date": dates, "x": 1.0})
    out = seasonal_reference(ref, "2026-10-02", window_days=21, min_rows=10)
    assert out["date"].min() >= pd.Timestamp("2025-10-01") and out["date"].max() <= pd.Timestamp("2025-10-23")


def test_seasonal_reference_falls_back_when_season_missing():
    import pandas as pd

    from ml.monitoring import seasonal_reference

    ref = pd.DataFrame({"date": pd.date_range("2025-12-01", "2026-02-28"), "x": 1.0})
    assert len(seasonal_reference(ref, "2026-07-15", min_rows=30)) == len(ref)


def test_psi_threshold_rises_for_small_samples_but_never_below_floor():
    from ml.monitoring import PSI_THRESHOLD, psi_bins, psi_threshold

    assert psi_threshold(100000, 100000, 10) == PSI_THRESHOLD
    assert psi_threshold(115, 70, psi_bins(70)) > PSI_THRESHOLD
    assert psi_bins(70) == 4 and psi_bins(1000) == 10 and psi_bins(5) == 3


def test_same_distribution_small_windows_rarely_breach_adjusted_threshold():
    import numpy as np

    from ml.monitoring import psi, psi_bins, psi_threshold

    rng = np.random.default_rng(3)
    breaches = 0
    for _ in range(200):
        a, b = rng.normal(100, 20, 115), rng.normal(100, 20, 70)
        breaches += psi(a, b, psi_bins(70)) > psi_threshold(115, 70, psi_bins(70))
    assert breaches / 200 < 0.05


def test_cooldown_suppresses_retrain_right_after_training():
    assert evaluate_retrain(None, 0, 38.0, 5, 40, days_since_last_training=2) == []
    assert evaluate_retrain(None, 0, 38.0, 5, 40, days_since_last_training=8) != []
    assert evaluate_retrain(None, 0, 38.0, 5, 40) != []
