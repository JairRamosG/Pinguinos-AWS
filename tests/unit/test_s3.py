"""Tests de almacenamiento S3 con moto: sin red ni credenciales reales."""

from __future__ import annotations

import io
import json
import pickle
from pathlib import Path

import boto3
import numpy as np
import pandas as pd
import pytest
from moto import mock_aws

from penguins_ml import serving, storage
from penguins_ml.config import TrainConfig
from penguins_ml.data import load_dataframe
from penguins_ml.provenance import dataset_fingerprint
from penguins_ml.train import main
from penguins_ml.transforms import build_pipeline

FEATURES = [
    "island",
    "bill_length_mm",
    "bill_depth_mm",
    "flipper_length_mm",
    "body_mass_g",
    "sex",
]


@pytest.fixture(autouse=True)
def _region(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def bucket():
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="test")
        yield "test"


def _s3():
    return boto3.client("s3", region_name="us-east-1")


def _csv_bytes(n: int = 60, seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
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
    return df.to_csv(index=False).encode()


def _config_file(tmp_path: Path, uri: str, output: str) -> str:
    path = tmp_path / "cloud.toml"
    path.write_text(
        f"""
[run]
seed = 7
folds = 3

[data]
uri = "{uri}"
target = "species"
features = {FEATURES!r}
categorical = ["island", "sex"]

[model]
type = "random_forest"
n_estimators = 5
max_depth = 3

[output]
uri = "{output}"
""",
        encoding="utf-8",
    )
    return str(path)


def _artifact_bytes(tmp_path: Path, run_id: str) -> tuple[bytes, dict]:
    config = TrainConfig.model_validate(
        {"data": {"uri": "x.csv", "features": FEATURES, "categorical": ["island", "sex"]}}
    )
    rng = np.random.default_rng(0)
    df = pd.DataFrame(
        {
            "island": rng.choice(["Biscoe", "Dream", "Torgersen"], 60),
            "sex": rng.choice(["male", "female"], 60),
            "bill_length_mm": rng.normal(45, 3, 60),
            "bill_depth_mm": rng.normal(17, 1.5, 60),
            "flipper_length_mm": rng.normal(200, 7, 60),
            "body_mass_g": rng.normal(4200, 400, 60),
            "species": rng.choice(["Adelie", "Chinstrap", "Gentoo"], 60),
        }
    )
    pipeline = build_pipeline(config).fit(df[FEATURES], df["species"])
    buffer = io.BytesIO()
    pickle.dump(pipeline, buffer)
    manifest = {
        "manifest_version": 1,
        "run_id": run_id,
        "config_hash": "sha256:abc123def456",
        "metrics": {"f1_macro": {"mean": 0.99, "std": 0.01}},
        "config": {"data": {"features": FEATURES}},
    }
    return buffer.getvalue(), manifest


# ── storage ─────────────────────────────────────────────────────────────────
def test_parse_s3_devuelve_bucket_y_clave() -> None:
    assert storage.parse_s3("s3://pinguinos-datos/raw/p.csv") == (
        "pinguinos-datos",
        "raw/p.csv",
    )


def test_parse_s3_falla_si_esta_incompleta() -> None:
    with pytest.raises(storage.StorageError, match="incompleta"):
        storage.parse_s3("s3://pinguinos-datos")
    with pytest.raises(storage.StorageError, match="No es una URI de S3"):
        storage.parse_s3("data/raw/p.csv")


def test_is_s3() -> None:
    assert storage.is_s3("s3://b/k")
    assert not storage.is_s3("data/raw/p.csv")


# ── lectura ─────────────────────────────────────────────────────────────────
def test_load_dataframe_local(tmp_path) -> None:
    path = tmp_path / "d.csv"
    path.write_bytes(b"a,b\n1,2\n")
    df = load_dataframe(str(path))
    assert list(df.columns) == ["a", "b"]


def test_load_dataframe_desde_s3(bucket) -> None:
    _s3().put_object(Bucket=bucket, Key="raw/penguins.csv", Body=_csv_bytes())
    df = load_dataframe(f"s3://{bucket}/raw/penguins.csv")
    assert len(df) == 60
    assert "species" in df.columns


def test_load_dataframe_falla_claro_si_no_existe(bucket) -> None:
    with pytest.raises(storage.StorageError, match="No existe el objeto"):
        load_dataframe(f"s3://{bucket}/raw/no.csv")


def test_fingerprint_local_y_s3_son_iguales(tmp_path, bucket) -> None:
    data = _csv_bytes()
    local = tmp_path / "d.csv"
    local.write_bytes(data)
    _s3().put_object(Bucket=bucket, Key="raw/d.csv", Body=data)

    assert dataset_fingerprint(local) == dataset_fingerprint(
        f"s3://{bucket}/raw/d.csv"
    )


def test_list_dirs_ordena_del_mas_reciente(bucket) -> None:
    for run in ("2026-01-01T00-00-00Z", "2026-02-01T00-00-00Z"):
        _s3().put_object(
            Bucket=bucket, Key=f"models/{run}/model.pkl", Body=b"x"
        )
    runs = storage.list_dirs(f"s3://{bucket}/models/")
    assert runs[0].endswith("2026-02-01T00-00-00Z/")


# ── train escribe en S3 ─────────────────────────────────────────────────────
def test_train_escribe_el_artefacto_en_s3(tmp_path, bucket) -> None:
    _s3().put_object(Bucket=bucket, Key="raw/penguins.csv", Body=_csv_bytes())
    config = _config_file(
        tmp_path, f"s3://{bucket}/raw/penguins.csv", f"s3://{bucket}/models/"
    )

    assert main(["--config", config]) == 0

    objects = _s3().list_objects_v2(Bucket=bucket, Prefix="models/")["Contents"]
    keys = sorted(obj["Key"] for obj in objects)
    names = {key.split("/")[2] for key in keys}
    assert names == {"model.pkl", "cv.json", "manifest.json"}

    run = keys[0].split("/")[1]
    manifest = json.loads(
        _s3().get_object(Bucket=bucket, Key=f"models/{run}/manifest.json")["Body"].read()
    )
    assert manifest["config"]["output"]["uri"] == f"s3://{bucket}/models/"
    assert manifest["data"]["etag"].startswith("sha256:")
    assert manifest["data"]["rows"] == 60


def test_train_devuelve_1_si_faltan_datos_en_s3(tmp_path, bucket) -> None:
    config = _config_file(
        tmp_path, f"s3://{bucket}/raw/no.csv", f"s3://{bucket}/models/"
    )
    assert main(["--config", config]) == 1


# ── serve lee de S3 ─────────────────────────────────────────────────────────
def test_serve_carga_el_modelo_desde_s3(tmp_path, bucket) -> None:
    run_id = "2026-01-01T00-00-00Z"
    model_bytes, manifest = _artifact_bytes(tmp_path, run_id)
    _s3().put_object(
        Bucket=bucket, Key=f"models/{run_id}/model.pkl", Body=model_bytes
    )
    _s3().put_object(
        Bucket=bucket,
        Key=f"models/{run_id}/manifest.json",
        Body=json.dumps(manifest).encode(),
    )

    artifact = serving.load_artifact(f"s3://{bucket}/models/{run_id}")
    assert artifact.run_id == run_id
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
    assert abs(sum(probabilities.values()) - 1.0) < 1e-9


def test_serve_toma_el_mas_reciente_si_la_uri_termina_en_barra(
    tmp_path, bucket
) -> None:
    for run_id in ("2026-01-01T00-00-00Z", "2026-03-01T00-00-00Z"):
        model_bytes, manifest = _artifact_bytes(tmp_path, run_id)
        _s3().put_object(
            Bucket=bucket, Key=f"models/{run_id}/model.pkl", Body=model_bytes
        )
        _s3().put_object(
            Bucket=bucket,
            Key=f"models/{run_id}/manifest.json",
            Body=json.dumps(manifest).encode(),
        )

    artifact = serving.load_artifact(f"s3://{bucket}/models/")
    assert artifact.run_id == "2026-03-01T00-00-00Z"


def test_serve_falla_si_no_hay_artefactos_en_s3(bucket) -> None:
    with pytest.raises(serving.ArtifactError, match="No hay artefactos"):
        serving.load_artifact(f"s3://{bucket}/vacio/")