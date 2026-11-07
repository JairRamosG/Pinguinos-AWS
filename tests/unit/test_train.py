"""Tests del CLI de entrenamiento y de la procedencia."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from penguins_ml import provenance
from penguins_ml.config import TrainConfig
from penguins_ml.train import main

FEATURES = [
    "island",
    "bill_length_mm",
    "bill_depth_mm",
    "flipper_length_mm",
    "body_mass_g",
    "sex",
]


def _csv(tmp_path, n: int = 60, seed: int = 0):
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
    path = tmp_path / "data.csv"
    df.to_csv(path, index=False)
    return path


def _config_file(tmp_path, csv_path, out_dir) -> str:
    path = tmp_path / "train.toml"
    path.write_text(
        f"""
[run]
seed = 7
folds = 3

[data]
uri = "{csv_path.as_posix()}"
target = "species"
features = {FEATURES!r}
categorical = ["island", "sex"]

[model]
type = "random_forest"
n_estimators = 10
max_depth = 3

[output]
uri = "{out_dir.as_posix()}"
""",
        encoding="utf-8",
    )
    return str(path)


# ── Invariantes de config ───────────────────────────────────────────────────
def test_target_no_puede_estar_en_features() -> None:
    with pytest.raises(ValidationError, match="no puede estar en features"):
        TrainConfig.model_validate(
            {"data": {"uri": "x", "features": ["species", "island"]}}
        )


def test_features_no_puede_estar_vacio() -> None:
    with pytest.raises(ValidationError, match="no puede estar vacío"):
        TrainConfig.model_validate({"data": {"uri": "x", "features": []}})


def test_features_no_admite_duplicados() -> None:
    with pytest.raises(ValidationError, match="duplicados"):
        TrainConfig.model_validate({"data": {"uri": "x", "features": ["a", "a"]}})


# ── Procedencia ─────────────────────────────────────────────────────────────
def test_config_hash_es_estable_ante_orden_de_claves() -> None:
    a = TrainConfig.model_validate({"data": {"uri": "x", "features": ["b", "a"]}})
    b = TrainConfig.model_validate({"data": {"features": ["b", "a"], "uri": "x"}})
    assert provenance.config_hash(a) == provenance.config_hash(b)


def test_config_hash_cambia_con_un_hiperparametro() -> None:
    a = TrainConfig.model_validate({"data": {"uri": "x", "features": ["a"]}})
    b = TrainConfig.model_validate(
        {"data": {"uri": "x", "features": ["a"]}, "model": {"n_estimators": 999}}
    )
    assert provenance.config_hash(a) != provenance.config_hash(b)


def test_dataset_fingerprint_es_estable_y_cambia_con_el_contenido(tmp_path) -> None:
    path = tmp_path / "d.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    etag_a, size_a = provenance.dataset_fingerprint(path)
    assert size_a == path.stat().st_size
    path.write_text("a,b\n1,3\n", encoding="utf-8")
    etag_b, _ = provenance.dataset_fingerprint(path)
    assert etag_a.startswith("sha256:")
    assert etag_a != etag_b


def test_git_sha_usa_la_variable_de_entorno(monkeypatch) -> None:
    monkeypatch.setenv("PENGUINS_GIT_SHA", "abc123")
    assert provenance.git_sha() == "abc123"


def test_git_sha_sin_variable_usa_git(monkeypatch) -> None:
    monkeypatch.delenv("PENGUINS_GIT_SHA", raising=False)
    sha = provenance.git_sha()
    assert sha == "unknown" or re.fullmatch(r"[0-9a-f]{40}", sha)


def test_git_dirty_usa_la_variable_de_entorno(monkeypatch) -> None:
    monkeypatch.setenv("PENGUINS_GIT_DIRTY", "1")
    assert provenance.git_dirty() is True
    monkeypatch.setenv("PENGUINS_GIT_DIRTY", "0")
    assert provenance.git_dirty() is False


def test_git_dirty_sin_variable_usa_git(monkeypatch) -> None:
    monkeypatch.delenv("PENGUINS_GIT_DIRTY", raising=False)
    assert isinstance(provenance.git_dirty(), bool)


# ── CLI de punta a punta ────────────────────────────────────────────────────
def test_run_completo_escribe_tres_archivos(tmp_path) -> None:
    csv_path = _csv(tmp_path)
    out = tmp_path / "artifacts"
    config = _config_file(tmp_path, csv_path, out)

    assert main(["--config", config]) == 0

    runs = list(out.iterdir())
    assert len(runs) == 1
    for name in ("model.pkl", "manifest.json", "cv.json"):
        assert (runs[0] / name).is_file()
    assert not list(out.glob(".tmp-*"))


def test_misma_config_produce_mismo_hash_y_metricas(tmp_path, monkeypatch) -> None:
    csv_path = _csv(tmp_path)
    out = tmp_path / "artifacts"
    config = _config_file(tmp_path, csv_path, out)

    for second in (0, 1):
        moment = datetime(2026, 1, 1, 0, 0, second, tzinfo=timezone.utc)
        monkeypatch.setattr(provenance, "utc_now", lambda moment=moment: moment)
        assert main(["--config", config]) == 0

    manifests = [json.loads(p.read_text()) for p in out.glob("*/manifest.json")]
    assert len(manifests) == 2
    assert manifests[0]["config_hash"] == manifests[1]["config_hash"]
    assert manifests[0]["metrics"] == manifests[1]["metrics"]


def test_config_invalida_devuelve_2(tmp_path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text('[data]\nuri = "x"\nfeatures = ["a"]\ncategorical = ["b"]\n')
    assert main(["--config", str(bad)]) == 2


def test_datos_inexistentes_devuelve_1(tmp_path) -> None:
    config = tmp_path / "cfg.toml"
    config.write_text(
        f'[data]\nuri = "{(tmp_path / "no.csv").as_posix()}"\nfeatures = {FEATURES!r}\n',
        encoding="utf-8",
    )
    assert main(["--config", str(config)]) == 1