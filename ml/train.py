"""
ml/train.py
────────────
Train baselines + regression + classification models for next-day AQI, log everything
to MLflow, register the best regressor as `aqi_next_day`, and (optionally) promote it
to the `champion` alias if it is not worse than the current champion on the same test window.

Usage:  python -m ml.train [--promote]
"""

from __future__ import annotations

import argparse
import logging
import os

import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
from mlflow import MlflowClient
from mlflow.models import infer_signature
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
)
from sklearn.model_selection import ParameterGrid
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from etl.aqi_calculator import aqi_category
from ml.models import DeltaRegressor
from ml.features import (
    CATEGORICAL_FEATURES,
    CATEGORY_ORDER,
    FEATURES,
    NUMERIC_FEATURES,
    build_feature_frame,
    chronological_split,
    load_daily,
    make_xy,
    merge_category,
)

logger = logging.getLogger("ml.train")

EXPERIMENT = "aqi_next_day"
MODEL_NAME = "aqi_next_day"
ALIAS = "champion"
SEED = 42
MIN_ROWS = 60
MIN_IMPROVEMENT = 0.05  # AQI points of test MAE a challenger must gain; ignores retrain noise


def _preprocessor(scale: bool) -> ColumnTransformer:
    num_steps = [("impute", SimpleImputer(strategy="median"))]
    if scale:
        num_steps.append(("scale", StandardScaler()))
    return ColumnTransformer([
        ("city", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
        ("num", Pipeline(num_steps), NUMERIC_FEATURES),
    ])


def _pipe(model, scale: bool) -> Pipeline:
    return Pipeline([("prep", _preprocessor(scale)), ("model", model)])


# name -> (estimator factory, needs_scaling, param grid tuned on validation)
REGRESSORS = {
    "ridge": (lambda **p: Ridge(**p), True, {"alpha": [0.1, 1.0, 10.0, 100.0]}),
    "random_forest": (
        lambda **p: RandomForestRegressor(n_estimators=200, random_state=SEED, n_jobs=-1, **p),
        False, {"max_depth": [6, 12], "min_samples_leaf": [1, 5]},
    ),
    "hist_gb": (
        lambda **p: HistGradientBoostingRegressor(random_state=SEED, **p),
        False, {"learning_rate": [0.05, 0.1], "max_depth": [3, 6]},
    ),
}
CLASSIFIERS = {
    "logreg": (
        lambda **p: LogisticRegression(max_iter=2000, class_weight="balanced", **p),
        True, {"C": [0.1, 1.0, 10.0]},
    ),
    "random_forest_clf": (
        lambda **p: RandomForestClassifier(
            n_estimators=200, class_weight="balanced", random_state=SEED, n_jobs=-1, **p),
        False, {"max_depth": [6, 12], "min_samples_leaf": [1, 5]},
    ),
    "hist_gb_clf": (
        lambda **p: HistGradientBoostingClassifier(class_weight="balanced", random_state=SEED, **p),
        False, {"learning_rate": [0.05, 0.1], "max_depth": [3, 6]},
    ),
}


def reg_metrics(y, pred, prefix: str) -> dict:
    return {
        f"{prefix}_rmse": float(np.sqrt(mean_squared_error(y, pred))),
        f"{prefix}_mae": float(mean_absolute_error(y, pred)),
        f"{prefix}_r2": float(r2_score(y, pred)),
    }


def clf_metrics(y, pred, prefix: str) -> dict:
    out = {f"{prefix}_accuracy": float(accuracy_score(y, pred))}
    for avg in ("macro", "weighted"):
        out[f"{prefix}_precision_{avg}"] = float(precision_score(y, pred, average=avg, zero_division=0))
        out[f"{prefix}_recall_{avg}"] = float(recall_score(y, pred, average=avg, zero_division=0))
        out[f"{prefix}_f1_{avg}"] = float(f1_score(y, pred, average=avg, zero_division=0))
    return out


def to_category(aqi_values) -> np.ndarray:
    return np.array([merge_category(aqi_category(v)) for v in aqi_values])


def _tune(factory, scale, grid, Xtr, ytr, Xva, yva, metric, delta=False):
    """Fit every grid point on train, pick best on validation. metric(y, pred) -> lower is better."""
    best = None
    for params in ParameterGrid(grid):
        pipe = _pipe(factory(**params), scale)
        if delta:
            pipe = DeltaRegressor(pipe)
        pipe = pipe.fit(Xtr, ytr)
        score = metric(yva, pipe.predict(Xva))
        if best is None or score < best[0]:
            best = (score, params, pipe)
    return best


def _run_meta(cuts: dict, tr, va, te, task: str) -> dict:
    return {
        "task": task,
        "train_start": str(tr["date"].min().date()),
        "train_end": cuts["train_end"],
        "val_end": cuts["val_end"],
        "test_end": str(te["date"].max().date()),
        "n_train": len(tr), "n_val": len(va), "n_test": len(te),
    }


def train(promote: bool = False) -> dict:
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000"))
    mlflow.set_experiment(EXPERIMENT)

    frame = build_feature_frame(load_daily())
    usable = frame[frame["y_aqi"].notna() & frame["aqi"].notna()]
    if len(usable) < MIN_ROWS:
        raise RuntimeError(f"Only {len(usable)} usable rows (need >= {MIN_ROWS}). Run the backfill first.")

    tr, va, te, cuts = _split_with_targets(usable)
    Xtr, ytr, _ = make_xy(tr); Xva, yva, _ = make_xy(va); Xte, yte, mte = make_xy(te)
    meta = _run_meta(cuts, tr, va, te, "regression")
    logger.info("rows train/val/test = %d/%d/%d  (%s)", len(tr), len(va), len(te), cuts)

    def log_common(run_name, task):
        mlflow.set_tags({"task": task})
        mlflow.log_params({**meta, "task": task})
        mlflow.log_dict({"features": FEATURES}, "features.json")

    results: dict[str, dict] = {}

    # ── Baselines (every real model must beat these) ─────────────────────────
    for name, col in [("baseline_persistence", "aqi"), ("baseline_7day_mean", "aqi_roll7_mean")]:
        with mlflow.start_run(run_name=name):
            log_common(name, "regression")
            pv = Xva[col].fillna(Xva["aqi"]); pt = Xte[col].fillna(Xte["aqi"])
            m = {**reg_metrics(yva, pv, "val"), **reg_metrics(yte, pt, "test")}
            mlflow.log_metrics(m)
            results[name] = m

    # ── Regression models ────────────────────────────────────────────────────
    best_reg = None
    variants = [(n, spec, False) for n, spec in REGRESSORS.items()] + \
               [(f"{n}_delta", spec, True) for n, spec in REGRESSORS.items()]
    for name, (factory, scale, grid), delta in variants:
        score, params, pipe = _tune(factory, scale, grid, Xtr, ytr, Xva, yva, mean_absolute_error, delta)
        with mlflow.start_run(run_name=name) as run:
            log_common(name, "regression")
            mlflow.set_tag("predicts_delta", str(delta))
            mlflow.log_params({f"hp_{k}": v for k, v in params.items()})
            pt = pipe.predict(Xte)
            m = {**reg_metrics(yva, pipe.predict(Xva), "val"), **reg_metrics(yte, pt, "test")}
            mlflow.log_metrics(m)
            mlflow.log_dict({"residuals": (yte.values - pt).round(3).tolist()}, "test_residuals.json")
            mlflow.sklearn.log_model(
                pipe, "model",
                signature=infer_signature(Xte, pt), input_example=Xte.head(3),
            )
            results[name] = m
            if best_reg is None or m["val_mae"] < best_reg["m"]["val_mae"]:
                best_reg = {"name": name, "run_id": run.info.run_id, "m": m, "pipe": pipe, "pred_test": pt}

    # ── Classification models (next-day AQI category) ────────────────────────
    Xtr_c, ytr_c, _ = make_xy(tr, "clf"); Xva_c, yva_c, _ = make_xy(va, "clf"); Xte_c, yte_c, _ = make_xy(te, "clf")
    with mlflow.start_run(run_name="baseline_persistence_category"):
        log_common("baseline_persistence_category", "classification")
        m = {**clf_metrics(yva_c, to_category(Xva_c["aqi"]), "val"), **clf_metrics(yte_c, to_category(Xte_c["aqi"]), "test")}
        mlflow.log_metrics(m)
        results["baseline_persistence_category"] = m

    neg_f1 = lambda y, p: -f1_score(y, p, average="macro", zero_division=0)  # noqa: E731
    for name, (factory, scale, grid) in CLASSIFIERS.items():
        score, params, pipe = _tune(factory, scale, grid, Xtr_c, ytr_c, Xva_c, yva_c, neg_f1)
        with mlflow.start_run(run_name=name):
            log_common(name, "classification")
            mlflow.log_params({f"hp_{k}": v for k, v in params.items()})
            pt = pipe.predict(Xte_c)
            m = {**clf_metrics(yva_c, pipe.predict(Xva_c), "val"), **clf_metrics(yte_c, pt, "test")}
            mlflow.log_metrics(m)
            labels = [c for c in CATEGORY_ORDER if c in set(yte_c) | set(pt)]
            mlflow.log_dict(
                {"labels": labels, "matrix": confusion_matrix(yte_c, pt, labels=labels).tolist()},
                "confusion_matrix.json",
            )
            mlflow.sklearn.log_model(pipe, "model", signature=infer_signature(Xte_c, pt))
            results[name] = m

    # Best regressor bucketed into categories, compared on the same classification metrics.
    with mlflow.start_run(run_name="best_regressor_as_classifier"):
        log_common("best_regressor_as_classifier", "classification")
        mlflow.set_tag("source_model", best_reg["name"])
        pv = to_category(best_reg["pipe"].predict(Xva))
        pt = to_category(best_reg["pred_test"])
        yv_c = to_category(yva); yt_c = to_category(yte)
        m = {**clf_metrics(yv_c, pv, "val"), **clf_metrics(yt_c, pt, "test")}
        mlflow.log_metrics(m)
        results["best_regressor_as_classifier"] = m

    # ── Register + promote ───────────────────────────────────────────────────
    version = _register_and_promote(best_reg, Xte, yte, mte, promote)
    summary = {"best_regressor": best_reg["name"], "version": version, "results": results}
    _print_table(results)
    return summary


def _split_with_targets(usable: pd.DataFrame):
    return chronological_split(usable)


def _register_and_promote(best, Xte, yte, mte, promote: bool):
    client = MlflowClient()
    mv = mlflow.register_model(f"runs:/{best['run_id']}/model", MODEL_NAME)
    version = mv.version
    challenger_mae = best["m"]["test_mae"]

    try:
        champ = client.get_model_version_by_alias(MODEL_NAME, ALIAS)
    except Exception:
        champ = None

    if champ is None:
        client.set_registered_model_alias(MODEL_NAME, ALIAS, version)
        logger.info("No champion yet -> v%s is champion", version)
        promoted = True
    else:
        champ_model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@{ALIAS}")
        champ_mae = float(mean_absolute_error(yte, champ_model.predict(Xte)))
        logger.info("Champion v%s test MAE=%.2f vs challenger v%s MAE=%.2f",
                    champ.version, champ_mae, version, challenger_mae)
        promoted = promote and challenger_mae < champ_mae - MIN_IMPROVEMENT
        if promoted:
            client.set_registered_model_alias(MODEL_NAME, ALIAS, version)
            logger.info("Promoted v%s to %s", version, ALIAS)
        else:
            logger.info("v%s kept registered but NOT promoted", version)

    if promoted:
        _write_backtest(version, best["pred_test"], yte, mte)
    client.set_model_version_tag(MODEL_NAME, version, "test_mae", f"{challenger_mae:.4f}")
    return version


def _write_backtest(version, pred, y, meta: pd.DataFrame) -> None:
    """Seed ml.predictions with test-set predictions so monitoring has history on day one."""
    try:
        from db.db_utils import execute_sql, upsert_dataframe

        execute_sql("DELETE FROM ml.predictions WHERE source = 'backtest'")
        df = pd.DataFrame({
            "city": meta["city"].values,
            "as_of": meta["date"].dt.date.values,
            "forecast_date": (meta["date"] + pd.Timedelta(days=1)).dt.date.values,
            "predicted_aqi": np.round(pred, 2),
            "predicted_category": to_category(pred),
            "actual_aqi": np.round(y.values).astype(int),
            "actual_category": to_category(y.values),
            "abs_error": np.abs(pred - y.values).round(2),
            "model_version": str(version),
            "source": "backtest",
        })
        upsert_dataframe(df, "predictions", "ml",
                         ["city", "forecast_date", "model_version", "source"],
                         ["as_of", "predicted_aqi", "predicted_category", "actual_aqi",
                          "actual_category", "abs_error"])
    except Exception as exc:  # table missing / DB down must not fail training
        logger.warning("Could not write backtest predictions: %s", exc)


def _print_table(results: dict) -> None:
    rows = [{"model": k, **{m: round(v, 3) for m, v in r.items() if m.startswith("test_")}}
            for k, r in results.items()]
    print(pd.DataFrame(rows).fillna("").to_string(index=False))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--promote", action="store_true", help="promote challenger to champion if not worse")
    args = ap.parse_args()
    out = train(promote=args.promote)
    print(f"\nBest regressor: {out['best_regressor']}  registered as {MODEL_NAME} v{out['version']}")
