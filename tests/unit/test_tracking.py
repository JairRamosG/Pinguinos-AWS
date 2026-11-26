"""Tests del registro opcional en MLflow."""

from __future__ import annotations

import pytest

from penguins_ml import tracking
from penguins_ml.config import TrainConfig


def _config(**overrides) -> TrainConfig:
    payload = {
        "data": {
            "uri": "data/raw/penguins.csv",
            "features": ["island", "bill_length_mm"],
            "categorical": ["island"],
        },
        **overrides,
    }
    return TrainConfig.model_validate(payload)


def test_params_aplanan_la_config_resuelta() -> None:
    with tracking.session(_config(), "run") as run_id:
        assert run_id is None          # sin uri, no-op
    params = tracking.params_from_config(_config(tracking={"uri": "http://mlflow:5000"}))
    assert params["run.seed"] == 42
    assert params["model.n_estimators"] == 100
    assert params["data.features"] == '["island", "bill_length_mm"]'
    assert "tracking.uri" not in params


def test_params_rechazan_claves_sensibles() -> None:
    with pytest.raises(ValueError, match="sensible"):
        tracking.params_from_payload({"aws": {"secret_access_key": "x"}})


def test_metricas_incluyen_promedio_y_por_fold() -> None:
    metrics = {"accuracy": {"mean": 0.9, "std": 0.01}}
    cv_payload = {"per_fold": [{"fold": 2, "accuracy": 0.85, "f1_macro": 0.75}]}
    flat = tracking.metrics_from_cv(metrics, cv_payload)
    assert (flat["accuracy_mean"], flat["accuracy_std"]) == (0.9, 0.01)
    assert flat["fold_2_f1_macro"] == 0.75


def test_la_sesion_cierra_con_el_estado_correcto(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeRun:
        class info:  # noqa: N801 - replica la forma de mlflow.ActiveRun
            run_id = "mlflow-123"

    class FakeMlflow:
        def set_tracking_uri(self, uri): pass
        def set_experiment(self, name): pass
        def start_run(self, run_name): return FakeRun()
        def log_params(self, params): calls.append(("params", params))
        def set_tags(self, tags): pass
        def end_run(self, status): calls.append(("end", status))

    monkeypatch.setattr(tracking, "_import_mlflow", lambda: FakeMlflow())
    config = _config(tracking={"uri": "http://mlflow:5000"})

    with tracking.session(config, "run-1", {"git_sha": "abc"}) as run_id:
        assert run_id == "mlflow-123"
    assert ("end", "FINISHED") in calls

    with pytest.raises(RuntimeError):
        with tracking.session(config, "run-1"):
            raise RuntimeError("se cayo la red")
    assert ("end", "FAILED") in calls