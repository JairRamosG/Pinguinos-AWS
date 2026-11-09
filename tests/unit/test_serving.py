"""Tests de la lógica de serving y de la UI de Streamlit."""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from penguins_ml import serving
from penguins_ml.config import TrainConfig
from penguins_ml.transforms import build_pipeline

FEATURES = [
    "island",
    "bill_length_mm",
    "bill_depth_mm",
    "flipper_length_mm",
    "body_mass_g",
    "sex",
]

SERVE_PATH = str(Path(__file__).resolve().parents[2] / "src" / "penguins_ml" / "serve.py")


@pytest.fixture(autouse=True)
def _clear_streamlit_cache():
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


def _df(n: int = 60, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "island": rng.choice(["Biscoe", "Dream", "Torgersen"], n),
            "sex": rng.choice(["male", "female"], n),
            "bill_length_mm": rng.normal(45, 3, n),
            "bill_depth_mm": rng.normal(17, 1.5, n),
            "flipper_length_mm": rng.normal(200, 7, n),
            "body_mass_g": rng.normal(4200, 400, n),
            "species": rng.choice(["Adelie", "Chinstrap", "Gentoo"], n),
        }
    )


def _artifact(tmp_path: Path, run_id: str = "2026-01-01T00-00-00Z") -> Path:
    config = TrainConfig.model_validate(
        {
            "data": {
                "uri": "x.csv",
                "features": FEATURES,
                "categorical": ["island", "sex"],
            }
        }
    )
    df = _df()
    pipeline = build_pipeline(config).fit(df[FEATURES], df["species"])

    run_dir = tmp_path / "artifacts" / run_id
    run_dir.mkdir(parents=True)
    with (run_dir / serving.MODEL).open("wb") as fh:
        pickle.dump(pipeline, fh)
    (run_dir / serving.MANIFEST).write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "run_id": run_id,
                "config_hash": "sha256:abc123def456",
                "metrics": {"f1_macro": {"mean": 0.99, "std": 0.01}},
                "config": {"data": {"features": FEATURES}},
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def test_latest_artifact_elige_el_mas_reciente(tmp_path) -> None:
    _artifact(tmp_path, "2026-01-01T00-00-00Z")
    newest = _artifact(tmp_path, "2026-02-01T00-00-00Z")
    assert serving.latest_artifact(tmp_path / "artifacts") == newest


def test_latest_artifact_falla_si_no_hay_directorio(tmp_path) -> None:
    with pytest.raises(serving.ArtifactError, match="No existe el directorio"):
        serving.latest_artifact(tmp_path / "no-existe")


def test_resolve_model_dir_precedencia(tmp_path, monkeypatch) -> None:
    explicit = _artifact(tmp_path, "2026-03-01T00-00-00Z")
    monkeypatch.setenv(serving.MODEL_URI_ENV, str(explicit))
    assert serving.resolve_model_dir() == explicit
    assert serving.resolve_model_dir(str(explicit)) == explicit


def test_resolve_model_dir_acepta_model_pkl(tmp_path) -> None:
    run_dir = _artifact(tmp_path)
    assert serving.resolve_model_dir(str(run_dir / serving.MODEL)) == run_dir


def test_load_artifact_falla_con_mensaje_claro_si_falta_el_modelo(tmp_path) -> None:
    run_dir = tmp_path / "artifacts" / "2026-01-01T00-00-00Z"
    run_dir.mkdir(parents=True)
    (run_dir / serving.MANIFEST).write_text("{}", encoding="utf-8")
    with pytest.raises(serving.ArtifactError, match="Falta model.pkl"):
        serving.load_artifact(str(run_dir))


def test_load_artifact_falla_si_falta_el_manifest(tmp_path) -> None:
    run_dir = _artifact(tmp_path)
    (run_dir / serving.MANIFEST).unlink()
    with pytest.raises(serving.ArtifactError, match="Falta manifest.json"):
        serving.load_artifact(str(run_dir))


def test_load_artifact_falla_si_el_pickle_esta_corrupto(tmp_path) -> None:
    run_dir = _artifact(tmp_path)
    (run_dir / serving.MODEL).write_bytes(b"no es un pickle")
    with pytest.raises(serving.ArtifactError, match="No se pudo cargar"):
        serving.load_artifact(str(run_dir))


def test_predict_devuelve_especie_y_probabilidades(tmp_path) -> None:
    artifact = serving.load_artifact(str(_artifact(tmp_path)))
    species, probabilities = serving.predict(
        artifact,
        {
            "island": "Dream",
            "sex": "male",
            "bill_length_mm": 45.0,
            "bill_depth_mm": 17.0,
            "flipper_length_mm": 200.0,
            "body_mass_g": 4200.0,
        },
    )
    assert species in artifact.pipeline.classes_
    assert set(probabilities) == set(map(str, artifact.pipeline.classes_))
    assert abs(sum(probabilities.values()) - 1.0) < 1e-9
    assert max(probabilities, key=probabilities.__getitem__) == species


def test_predict_no_depende_del_orden_del_diccionario(tmp_path) -> None:
    artifact = serving.load_artifact(str(_artifact(tmp_path)))
    values = {
        "body_mass_g": 4200.0,
        "sex": "female",
        "bill_length_mm": 45.0,
        "island": "Biscoe",
        "flipper_length_mm": 200.0,
        "bill_depth_mm": 17.0,
    }
    species, _ = serving.predict(artifact, values)
    assert species in artifact.pipeline.classes_


# ── UI con AppTest ──────────────────────────────────────────────────────────
def test_ui_carga_y_muestra_la_version_del_modelo(tmp_path, monkeypatch) -> None:
    run_dir = _artifact(tmp_path)
    monkeypatch.setenv(serving.MODEL_URI_ENV, str(run_dir))

    at = AppTest.from_file(SERVE_PATH).run(timeout=15)
    assert not at.exception
    contents = " ".join(element.value for element in at.sidebar.markdown)
    assert run_dir.name in contents
    assert "abc123def456" in contents


def test_ui_predice_al_enviar_el_formulario(tmp_path, monkeypatch) -> None:
    run_dir = _artifact(tmp_path)
    monkeypatch.setenv(serving.MODEL_URI_ENV, str(run_dir))

    at = AppTest.from_file(SERVE_PATH).run(timeout=15)
    at.selectbox[0].set_value("Dream")
    at.selectbox[1].set_value("male")
    at.number_input[0].set_value(45.0)
    at.number_input[1].set_value(17.0)
    at.number_input[2].set_value(200.0)
    at.number_input[3].set_value(4200.0)
    at.button[0].click().run(timeout=15)

    assert not at.exception
    assert any("Especie" in element.value for element in at.success)


def test_ui_muestra_error_claro_si_falta_el_artefacto(tmp_path, monkeypatch) -> None:
    run_dir = _artifact(tmp_path)
    monkeypatch.setenv(serving.MODEL_URI_ENV, str(run_dir))
    (run_dir / serving.MODEL).unlink()

    at = AppTest.from_file(SERVE_PATH).run(timeout=15)
    assert not at.exception
    assert any("No se pudo cargar el modelo" in element.value for element in at.error)