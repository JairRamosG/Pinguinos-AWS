"""Carga de datos desde local o S3"""

from __future__ import annotations

import pandas as pd

def load_dataframe(uri: str) -> pd.DataFrame:
    raise NotImplementedError("Se implementa en el issue 8")

def validate_schema(df: pd.DataFrame, features: list[str], target:str) -> None:
    """
    Verifica que el DataFrame contenga todo lo que la configuración exige
    Falla temprano y con nombre en lugar de reventear dentro del pipeline
    """

    if df.empty:
        raise ValueError("El DataFrame está vacío")

    required = [*features, target]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas requeridas en el DataFrame: {missing}")
