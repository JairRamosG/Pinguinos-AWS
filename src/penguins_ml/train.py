"""Entry point de entrenamiento"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import shutil
import sys
import tempfile

from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import ValidationError
from sklearn.model_selection import StratifiedKFold, cross_validate

from penguins_ml import provenance
from penguins_ml.config import TrainConfig, load_config
from penguins_ml.data import validate_schema
from penguins_ml.transforms import build_pipeline

log = logging.getLogger("penguins_ml.train")

SCORES = {"accuracy": "accuracy", "f1_macro": "f1_macro"}

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog = "penguins_ml.",
        description= "Entrena el pipeline, calcula CV y escribe el artefacto"
    )
    parser.add_argument(
        "--config",
        action = "append",
        required = True,
        metavar = "PATH",
        help = "TOML de configuracion, repetible en orden de precedencia"
    )
    parser.add_argument(
        "--data-uri",
        default=None,
        help="override explicito de data.uri"
    )
    parser.add_argument(
        "--output-uri",
        default=None,
        help="override explicito de output.uri"        
    )
    return parser.parse_args(argv)

def _apply_overrides(
        config: TrainConfig, 
        args: argparse.Namespace) -> tuple[TrainConfig, dict[str, Any]]:
    """
    Los flags de CLI son override explicito y quedan registrados en el manifest
    """
    overrides = {"data_uri": args.data_uri, "output_uri": args.output_uri}
    if not any(overrides.values()):
        return config, overrides

    payload = config.model_dump()
    if args.data_uri:
        payload["data"]["uri"] = args.data_uri
    if args.output_uri:
        payload["output"]["uri"] = args.output_uri 
    return TrainConfig.model_validate(payload), overrides   

def _config_error(exc: Exception) -> str:
    """
    Resume un ValidationError de Pydantic en una linea legible
    """ 
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
    return str(exc)

def _validate_folds(y: pd.Series, folds: int) -> None:
    """
    Una clase con menos muestras que folds rompe el StratifiedKFolds
    """
    counts = Counter(y)
    smallest_class, smallest = min(counts.items(), key = lambda item: item[1])
    if folds > smallest:
        raise ValueError(
            f"folds = {folds} excede la clase minoritaria"
            f"({smallest} muestra de {smallest_class!r})"
        )

def _metrics(result: dict[str, Any]) -> dict[str, dict[str, float]]:
    return{
        name:{
            "mean": float(result[f"test_{name}"].mean()),
            "std": float(result[f"test_{name}"].std())
        }
        for name in SCORES
    }

def _cv_payload(result: dict[str, Any], config: TrainConfig) -> dict[str, Any]:
    indices = result["indices"]
    per_fold = [
        {
            "fold": i,
            "accuracy": float(result["test_accuracy"][i]),
            "f1_macro": float(result["test_f1_macro"][i]),
            "n_train": int(len(train_idx)),
            "n_test": int(len(test_idx))
        }
        for i, (train_idx, test_idx) in enumerate(zip(indices["train"], indices["test"]))
    ]

    return{
        "strategy": "stratified_kfold",
        "folds": config.run.folds,
        "shuffle": True,
        "seed": config.run.seed,
        "per_fold": per_fold
    }

def _write_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())

def _write_artifact(
    output_dir: Path,
    run: str,
    pipeline: Any,
    manifest: dict[str, Any],
    cv_payload: dict[str, Any],
    ) -> Path:
    """
    Escribe en un directorio temporal y renombra: nunca deja un artefacto a medias
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    final = output_dir / run
    if final.exists():
        raise FileExistsError(f"El artefacto ya existe: {final}")

    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir = output_dir))
    try:
        with(tmp / "model.pkl").open("wb") as fh:
            pickle.dump(pipeline, fh, protocol=pickle.HIGHEST_PROTOCOL)
            fh.flush()
            os.fsync(fh.fileno())
        _write_json(tmp / "manifest.json", manifest)
        _write_json(tmp / "cv.json", cv_payload)
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level = logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr
    )
    args = _parse_args(argv)

    # Fase 1: conifg. para fallar antes de tocar los datos
    try:
        config = load_config(*args.config)
        config, overrides = _apply_overrides(config, args)
        pipeline = build_pipeline(config)
    except (ValueError, OSError) as exc:
        log.error(f"Configuración no valida: {_config_error(exc)}")
        return 2

    # Fase 2: datos
    features, target = config.data.features, config.data.target
    try:
        df = pd.read_csv(config.data.uri)
        validate_schema(df, features, target)
        _validate_folds(df[target], config.run.folds)
        etag, size_bytes = provenance.dataset_fingerprint(config.data.uri)
    except (ValueError, OSError) as exc:
        log.error(f"Datos no validos: {exc}")
        return 1

    # Fase 3: CV para métricas y fit al final sobre todos los datos
    try:
        cv = StratifiedKFold(
            n_splits= config.run.folds,
            shuffle = True,
            random_state= config.run.seed
        )
        result = cross_validate(
            pipeline, 
            df[features],
            df[target],
            cv = cv,
            scoring = SCORES,
            error_score = "raise",
            return_indices = True
        )
        metrics = _metrics(result)
        cv_payload = _cv_payload(result, config)
        pipeline.fit(df[features], df[target])

    except Exception as exc:
        log.error(f"Entrenamiento fallido: {exc}")
        return 1

    # Fase 4: artefacto
    now = provenance.utc_now()
    run = provenance.run_id(now)
    manifest = provenance.build_manifest(
        config = config,
        config_sources = [str(Path(path)) for path in args.config],
        overrides = overrides,
        run = run,
        created_at= provenance.iso(now),
        data_uri= config.data.uri,
        etag = etag,
        size_bytes = size_bytes,
        rows = len(df),
        metrics = metrics
    )

    try:
        final = _write_artifact(
            Path(config.output.uri),
            run,
            pipeline,
            manifest, 
            cv_payload
        )
    except (OSError, ValueError) as exc:
        log.error(f"No se pudo escribir el artefacto: {exc}")
        return 1

    log.info(f"artefacto: {final}")
    log.info(f"manifest: {manifest['config_hash']}")
    for name, values in metrics.items():
        log.info(f"{name}: {values['mean']:.4f} +- {values['std']:.4f}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
