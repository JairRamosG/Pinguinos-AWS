"""Carga del artefacto y predicción: lógica pura separado de Streamlit
porque permite testear sin levantar una sesión.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger("penguins_ml.serving")

MODEL = "model.pkl"
MANIFEST = "manifest.json"
MODEL_URI_ENV = "PENGUINS_MODEL_URI"


class ArtifactError(RuntimeError):
    """Error claro cuando el artefacto no existe o no se puede cargar."""


@dataclass(frozen=True)
class Artifact:
    pipeline: Any
    manifest: dict[str, Any]
    model_dir: Path

    @property
    def run_id(self) -> str:
        return str(self.manifest.get("run_id", self.model_dir.name))

    @property
    def config_hash(self) -> str:
        return str(self.manifest.get("config_hash", ""))

    @property
    def metrics(self) -> dict[str, dict[str, float]]:
        return self.manifest.get("metrics", {})

    @property
    def features(self) -> list[str]:
        """El pipeline sabe con qué columnas se entrenó; si no, el manifest."""
        names = getattr(self.pipeline, "feature_names_in_", None)
        if names is not None:
            return list(names)
        return list(self.manifest.get("config", {}).get("data", {}).get("features", []))


def latest_artifact(artifacts_root: str | Path = "artifacts") -> Path:
    """El artefacto más reciente bajo artifacts/ (run_id ordena lexicográficamente)."""
    root = Path(artifacts_root)
    if not root.is_dir():
        raise ArtifactError(
            f"No existe el directorio de artefactos: {root}. "
            "Entrená primero: python -m penguins_ml.train --config configs/base.toml "
            "--config configs/local.toml"
        )
    runs = sorted(
        (p for p in root.iterdir() if p.is_dir() and (p / MODEL).is_file()),
        reverse=True,
    )
    if not runs:
        raise ArtifactError(f"No hay artefactos en {root}. Entrená primero.")
    return runs[0]


def resolve_model_dir(
    model_uri: str | None = None,
    artifacts_root: str | Path = "artifacts",
) -> Path:
    """Precedencia: parámetro > PENGUINS_MODEL_URI > último artefacto."""
    uri = model_uri or os.getenv(MODEL_URI_ENV) or ""
    if not uri:
        return latest_artifact(artifacts_root)

    path = Path(uri)
    if path.is_dir():
        return path
    if path.name == MODEL:
        return path.parent
    raise ArtifactError(
        f"{MODEL_URI_ENV} debe apuntar a un directorio de artefacto o a {MODEL}: {path}"
    )


def load_artifact(
    model_uri: str | None = None,
    artifacts_root: str | Path = "artifacts",
) -> Artifact:
    model_dir = resolve_model_dir(model_uri, artifacts_root)
    model_path = model_dir / MODEL
    manifest_path = model_dir / MANIFEST

    if not model_path.is_file():
        raise ArtifactError(
            f"Falta {MODEL} en {model_dir}. El artefacto está incompleto."
        )
    if not manifest_path.is_file():
        raise ArtifactError(
            f"Falta {MANIFEST} en {model_dir}. El artefacto está incompleto."
        )

    try:
        with model_path.open("rb") as fh:
            pipeline = pickle.load(fh)
    except Exception as exc:
        raise ArtifactError(f"No se pudo cargar {model_path}: {exc}") from exc

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArtifactError(f"manifest inválido en {manifest_path}: {exc}") from exc

    artifact = Artifact(pipeline=pipeline, manifest=manifest, model_dir=model_dir)
    log.info(
        "modelo %s cargado desde %s (config_hash=%s)",
        artifact.run_id,
        model_dir,
        artifact.config_hash[:12],
    )
    return artifact


def predict(artifact: Artifact, values: dict[str, Any]) -> tuple[str, dict[str, float]]:
    """Devuelve la especie más probable y todas las probabilidades."""
    missing = [name for name in artifact.features if name not in values]
    if missing:
        raise ValueError(f"Faltan valores para: {missing}")

    row = pd.DataFrame(
        [[values[name] for name in artifact.features]],
        columns=artifact.features,
    )
    raw = artifact.pipeline.predict_proba(row)[0]
    classes = [str(label) for label in artifact.pipeline.classes_]
    probabilities = {label: float(p) for label, p in zip(classes, raw)}
    species = max(probabilities, key=probabilities.__getitem__)
    return species, probabilities