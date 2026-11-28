"""Registro opcional en MLflow: no-op si `tracking.uri` esta vacio."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

from penguins_ml.config import TrainConfig

_FORBIDDEN = ("secret", "token", "password", "access_key", "private_key")

# Se prende solo mientras una corrida esta abierta: los helpers no importan mlflow
# cuando el tracking esta apagado (evita el costo y el ruido del import).
_active = False



class TrackingError(RuntimeError):
    """El servidor no acepto una operacion: la corrida no debe seguir como si nada."""


def _import_mlflow() -> ModuleType | None:
    """Devuelve el modulo, o None si el extra 'mlflow' no esta instalado."""
    try:
        import mlflow
    except ImportError:
        return None
    return mlflow


def _flatten(value: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _flatten(child, f"{prefix}.{key}" if prefix else key)
    elif isinstance(value, (list, tuple)):
        yield prefix, json.dumps(list(value), ensure_ascii=False)
    elif value is not None:
        yield prefix, value


def params_from_payload(payload: dict[str, Any]) -> dict[str, str | int | float | bool]:
    """Aplana un payload de config a parametros escalares de MLflow."""
    params: dict[str, str | int | float | bool] = {}
    for key, value in _flatten(payload):
        if any(bad in key.lower() for bad in _FORBIDDEN):
            raise ValueError(f"clave sensible en la configuracion: {key}")
        params[key] = value
    return params


def params_from_config(config: TrainConfig) -> dict[str, str | int | float | bool]:
    """El bloque tracking se excluye: describe el transporte, no el experimento."""
    return params_from_payload(config.model_dump(mode="json", exclude={"tracking"}))


def metrics_from_cv(metrics: dict[str, dict[str, float]], cv_payload: dict[str, Any]) -> dict[str, float]:
    flat = {f"{name}_mean": values["mean"] for name, values in metrics.items()}
    flat |= {f"{name}_std": values["std"] for name, values in metrics.items()}
    for fold in cv_payload["per_fold"]:
        index = fold["fold"]
        flat[f"fold_{index}_accuracy"] = fold["accuracy"]
        flat[f"fold_{index}_f1_macro"] = fold["f1_macro"]
    return flat


@contextmanager
def session(config: TrainConfig, run: str, tags: dict[str, str] | None = None) -> Iterator[str | None]:
    """Abre una corrida y devuelve su run_id, o None si el tracking esta apagado."""
    if not config.tracking.uri.strip():
        yield None
        return

    mlflow = _import_mlflow()
    if mlflow is None:
        raise RuntimeError(
            "tracking.uri esta definido pero el extra 'mlflow' no esta instalado. "
            "Ejecuta: uv sync --extra mlflow"
        )

    params = params_from_config(config)
    mlflow.set_tracking_uri(config.tracking.uri)
    mlflow.set_experiment(config.tracking.experiment)
    active = mlflow.start_run(run_name=run)
    global _active
    _active = True
    try:
        mlflow.log_params(params)
        mlflow.set_tags({"provenance_run_id": run, **(tags or {})})
        yield active.info.run_id
    except BaseException:
        mlflow.end_run(status="FAILED")
        raise
    else:
        mlflow.end_run(status="FINISHED")
    finally:
        _active = False


def log_metrics(values: dict[str, float]) -> None:
    if not _active:
        return
    mlflow = _import_mlflow()
    if mlflow is None or mlflow.active_run() is None:
        return
    try:
        mlflow.log_metrics(values)
    except Exception as exc:
        raise TrackingError(f"MLflow rechazo las metricas: {exc}") from exc


def log_artifacts(directory: Path) -> None:
    if not _active:
        return
    mlflow = _import_mlflow()
    if mlflow is None or mlflow.active_run() is None:
        return
    try:
        mlflow.log_artifacts(str(directory))
    except Exception as exc:
        raise TrackingError(f"MLflow rechazo los artefactos: {exc}") from exc