from pathlib import Path

import pytest
from pydantic import ValidationError

from penguins_ml.config import load_config


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_config_base_carga_con_defaults(tmp_path: Path) -> None:
    base = _write(
        tmp_path / "base.toml",
        '[data]\nuri = "data/raw/penguins.csv"\nfeatures = ["island", "bill_length_mm"]\n',
    )

    config = load_config(base)

    assert config.data.uri == "data/raw/penguins.csv"
    assert config.run.folds == 5
    assert config.run.seed == 42
    assert config.output.uri == "artifacts/"


def test_el_override_gana_sobre_la_base(tmp_path: Path) -> None:
    base = _write(
        tmp_path / "base.toml",
        '[data]\nuri = "local.csv"\nfeatures = ["island"]\n',
    )
    override = _write(
        tmp_path / "cloud.toml",
        '[data]\nuri = "s3://bucket/raw.csv"\n',
    )

    config = load_config(base, override)

    assert config.data.uri == "s3://bucket/raw.csv"
    assert config.data.features == ["island"]  # se conserva de la base


def test_variable_de_entorno_gana_sobre_archivo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _write(
        tmp_path / "base.toml",
        '[data]\nuri = "local.csv"\nfeatures = ["island"]\n',
    )
    monkeypatch.setenv("PENGUINS_DATA_URI", "s3://bucket/override.csv")

    config = load_config(base)

    assert config.data.uri == "s3://bucket/override.csv"


def test_config_invalida_falla_con_mensaje_claro(tmp_path: Path) -> None:
    bad = _write(
        tmp_path / "bad.toml",
        '[data]\nfeatures = ["island"]\n',  # falta uri
    )

    with pytest.raises(ValidationError) as exc_info:
        load_config(bad)

    assert "uri" in str(exc_info.value)


def test_valor_fuera_de_rango_falla(tmp_path: Path) -> None:
    bad = _write(
        tmp_path / "bad.toml",
        '[run]\nfolds = 1\n[data]\nuri = "x.csv"\nfeatures = ["island"]\n',
    )

    with pytest.raises(ValidationError):
        load_config(bad)