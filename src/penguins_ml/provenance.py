"""
Recolección de procedencia para un artefacto de entrenamiento

Todas las funciones son puras o de lectura: no entrenan ni escriben.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import platform
import subprocess
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import sklearn

from penguins_ml.config import TrainConfig

from penguins_ml import storage

_CHUNK = 64 * 1026

def _git(*args: str)-> str:
    """
    Devualve stdout de git, o cadena vacía si no hay repo de git
    """
    try:
        completed = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    return completed.stdout.strip()

def git_sha() -> str:
    """Usa la variable de entorno inyectada en el build; si no, git."""
    return os.getenv("PENGUINS_GIT_SHA") or _git("rev-parse", "HEAD") or "unknown"

def git_dirty() -> bool:
    """Usa la variable de entorno inyectada en el build; si no, git."""
    env = os.getenv("PENGUINS_GIT_DIRTY")
    if env is not None:
        return env == "1"
    return bool(_git("status", "--porcelain"))

def dataset_fingerprint(uri: str | Path) -> tuple[str, int]:
    """
    SHA-256 del contenido y el tamaño en bytes, en streaming

    Soporta rutas locales y 's3://', resolviendo con modulo storage
    """
    digest = hashlib.sha256()
    size = 0
    with storage.open_read(str(uri)) as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return f"sha256:{digest.hexdigest()}", size

def config_hash(config: TrainConfig) -> str:
    """
    Hash canónico de la configuración resuelta (independiente del orden de claves)
    """
    payload = json.dumps(
        config.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"

def utc_now() -> str:
    """
    Fecha y hora UTC
    """
    return datetime.now(timezone.utc)

def run_id(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H-%M-%SZ")

def iso(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H-%M-%SZ")

def build_manifest(
    *,
    config: TrainConfig,
    config_sources: list[str],
    overrides: dict[str, Any],
    run: str,
    created_at: str,
    data_uri: str,
    etag:str,
    size_bytes: int,
    rows: int,
    metrics: dict[str, float]
) -> dict[str, Any]:
    """
    Construye el manifest (schema v1) con procedencia completa
    """
    return{
        "manifest_version" : 1,
        "run_id" : run,
        "created_at" : created_at,
        "code" : {"git_sha": git_sha(), "git_dirty": git_dirty()},
        "data": {
            "uri": data_uri,
            "etag": etag,
            "rows": rows,
            "bytes": size_bytes
        },
        "runtime": {
            "python_version": platform.python_version(),
            "sklearn_version": sklearn.__version__,
            "pandas_version": pd.__version__
        },
        "config": config.model_dump(mode = "json"),
        "config_hash": config_hash(config),
        "config_sources": config_sources,
        "overrides": overrides,
        "cv": {
            "strategy": "stratified_kfold",
            "folds": config.run.folds,
            "shuffle": True,
            "seed": config.run.seed
        },
        "metrics": metrics,
        "artifact": {
            "model_file": "model.pkl",
            "pickle_protocol": pickle.HIGHEST_PROTOCOL
        },
        "tracking_uri": config.tracking.uri
    }

