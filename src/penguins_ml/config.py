""" Carga y validación de la configuración del proyecto desde los toml"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

class RunConfig(BaseModel):
    seed: int = 42
    folds: int = Field(default = 5, ge=2, le=10)
    stratify: bool = True

class DataConfig(BaseModel):
    uri: str
    target: str = "species"
    features: list[str]
    categorical: list[str] = []

    @model_validator(mode="after")
    def _validar_columnas(self) -> "DataConfig":
        """
        Invariantes que solo dependen de la configuración, no del dataset
        """
        # No se especifican características    
        if not self.features:
            raise ValueError("features no puede estar vacío")

        # Features duplicados
        duplicados = sorted(f for f in set(self.features) if self.features.count(f) > 1)
        if duplicados:
            raise ValueError(f"Existen features duplicados: {duplicados}")

        # Fuga de etiqueta para que el modelo no sea feature
        if self.target in self.features:
            raise ValueError(f"target {self.target!r} no puede estar en features")

        # categorias no especificadas en features
        unknown = [c for c in self.categorical if c not in self.features]
        if unknown:
            raise ValueError(f"categorical contiene columnas ausentes en features: {unknown}")
        return self

class ModelConfig(BaseModel):
    type: str = "random_forest"
    n_estimators: int = Field(default=100, ge=1)
    max_depth: int | None = Field(default=None, ge=1)
    max_iter: int = Field(default=1000, ge=1)

class OutputConfig(BaseModel):
    uri: str = "artifacts/"

class TrackingConfig(BaseModel):
    uri: str = ""

class TrainConfig(BaseModel):
    run: RunConfig = Field(default_factory=RunConfig)
    data: DataConfig
    model: ModelConfig = Field(default_factory=ModelConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    tracking: TrackingConfig = Field(default_factory=TrackingConfig)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """ Mezcla recursiva, las claves de override reemplazan a las de base, si son diccionarios se mezclan recursivamente"""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result

def load_config(*paths: str | Path) -> TrainConfig:
    """ Carga y mezcla varios toml, aplica overrides de entorno y valida".
    
    Precedencia de menor a mayor:
        defaults del modelo < archivos en orden < variables de entorno
    """

    merged: dict[str, Any] = {}
    for path in paths:
        with open(path, "rb") as fh:
            merged = _deep_merge(merged, tomllib.load(fh))

    if value := os.getenv("PENGUINS_DATA_URI"):
        merged.setdefault("data", {})["uri"] = value
    if value := os.getenv("PENGUINS_OUTPUT_URI"):
        merged.setdefault("output", {})["uri"] = value

    return TrainConfig.model_validate(merged)