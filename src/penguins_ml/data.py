"""Carga de datos desde local o S3"""

from __future__ import annotations

import pandas as pd
from penguins_ml import storage

def load_dataframe(uri: str) -> pd.DataFrame:
    """
    Carga un CSV desde una ruta local o desde `s3://bucket/clave`.

    El resto del código no cambia: la config declara la URI y este módulo
    decide cómo leerla.
    """
    if storage.is_s3(uri):
        try:
            with storage.open_read(uri) as fh:
                return pd.read_csv(fh)
        except storage.StorageError:
            raise
        except ValueError as exc:
            raise storage.StorageError(f"CSV inválido en {uri}: {exc}") from exc
    try:
        return pd.read_csv(uri)
    except OSError as exc:
        raise storage.StorageError(f"No se pudo leer {uri}: {exc}") from exc
    except ValueError as exc:
        raise storage.StorageError(f"CSV inválido en {uri}: {exc}") from exc

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
