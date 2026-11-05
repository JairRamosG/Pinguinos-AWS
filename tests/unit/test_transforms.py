import pickle

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from penguins_ml.config import load_config
from penguins_ml.data import validate_schema
from penguins_ml.transforms import build_pipeline, _build_preprocessor

FEATURES = [
    "island",
    "bill_length_mm",
    "bill_depth_mm",
    "flipper_length_mm",
    "body_mass_g",
    "sex",
]


def _config(
    tmp_path,
    *,
    model_type: str = "random_forest",
    n_estimators: int = 10,
    max_depth: int = 3,
):
    text = f"""
[data]
uri = "x.csv"
target = "species"
features = {FEATURES!r}
categorical = ["island", "sex"]

[model]
type = "{model_type}"
n_estimators = {n_estimators}
max_depth = {max_depth}
"""
    path = tmp_path / "test.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path)


def _df(n: int = 60, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
        {
            "island": rng.choice(["Biscoe", "Dream", "Torgersen"], n),
            "sex": rng.choice(["male", "female", None], n),
            "bill_length_mm": rng.normal(45, 3, n),
            "bill_depth_mm": rng.normal(17, 1.5, n),
            "flipper_length_mm": rng.normal(200, 7, n),
            "body_mass_g": rng.normal(4200, 400, n),
            "species": rng.choice(["Adelie", "Chinstrap", "Gentoo"], n),
        }
    )
    # Introduce valores faltantes reales
    df.loc[df.index[:5], "bill_length_mm"] = np.nan
    return df


# ─── Validación de esquema ──────────────────────────────────────────────────
def test_schema_valido_no_lanza() -> None:
    validate_schema(_df(), FEATURES, "species")


def test_schema_falla_si_falta_columna() -> None:
    with pytest.raises(ValueError, match="Faltan columnas"):
        validate_schema(_df(), [*FEATURES, "no_existe"], "species")


def test_schema_falla_si_esta_vacio() -> None:
    with pytest.raises(ValueError, match="vacío"):
        validate_schema(_df().iloc[0:0], FEATURES, "species")


# ─── Preprocesador ──────────────────────────────────────────────────────────
def test_preprocesador_maneja_nulos(tmp_path) -> None:
    config = _config(tmp_path)
    df = _df()
    pre = _build_preprocessor(config)

    transformed = pre.fit_transform(df[FEATURES])
    assert transformed.shape[0] == len(df)
    assert not np.isnan(np.asarray(transformed, dtype=float)).any()


# ─── Pipeline completo ──────────────────────────────────────────────────────
def test_pipeline_entrena_y_predice(tmp_path) -> None:
    config = _config(tmp_path)
    df = _df()
    pipeline = build_pipeline(config)

    pipeline.fit(df[FEATURES], df["species"])
    predictions = pipeline.predict(df[FEATURES])

    assert len(predictions) == len(df)
    assert set(predictions).issubset(set(df["species"]))


def test_categoria_desconocida_no_rompe(tmp_path) -> None:
    config = _config(tmp_path)
    df = _df()
    df_sin_torgersen = df[df["island"] != "Torgersen"]

    pipeline = build_pipeline(config)
    pipeline.fit(df_sin_torgersen[FEATURES], df_sin_torgersen["species"])

    nuevo = df_sin_torgersen.iloc[[0]].copy()
    nuevo["island"] = "Torgersen"  # categoría nunca vista

    assert len(pipeline.predict(nuevo[FEATURES])) == 1


def test_modelo_no_soportado_falla(tmp_path) -> None:
    config = _config(tmp_path, model_type="svm")

    with pytest.raises(ValueError, match="no soportado"):
        build_pipeline(config)


def test_configs_distintas_producen_modelos_distintos(tmp_path) -> None:
    pipeline_a = build_pipeline(_config(tmp_path, n_estimators=10))
    pipeline_b = build_pipeline(_config(tmp_path, n_estimators=50))

    assert pipeline_a.named_steps["estimator"].n_estimators == 10
    assert pipeline_b.named_steps["estimator"].n_estimators == 50


def test_pickle_roundtrip(tmp_path) -> None:
    config = _config(tmp_path)
    df = _df()

    pipeline = build_pipeline(config)
    pipeline.fit(df[FEATURES], df["species"])

    restored = pickle.loads(pickle.dumps(pipeline))
    assert list(restored.predict(df[FEATURES])) == list(pipeline.predict(df[FEATURES]))


# ─── Validación de la config ────────────────────────────────────────────────
def test_categorical_debe_estar_en_features(tmp_path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(
        '[data]\nuri = "x.csv"\nfeatures = ["a"]\ncategorical = ["b"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_config(path)