"""Construcción del pipeline de preprocesamiento"""

from __future__ import annotations

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from penguins_ml.config import TrainConfig

def _build_preprocessor(config: TrainConfig) -> ColumnTransformer:
    """ 
    Construye el preprocesamiento a partir de la configuración

    Numéricas: imputación por mediana + escalado
    Categóricas: imputación por moda + one-hot encoding
    """

    categorical = list(config.data.categorical)
    numeric = [f for f in config.data.features if f not in categorical]

    numeric_steps = Pipeline(
        steps = [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler())
        ]
    )

    categorical_steps = Pipeline(
        steps = [
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("encoder", OneHotEncoder(handle_unknown="ignore"))
        ]
    )

    transformers = []

    if numeric:
        transformers.append(("numeric", numeric_steps, numeric))
    if categorical:
        transformers.append(("categorical", categorical_steps, categorical))

    return ColumnTransformer(transformers = transformers, remainder = "drop")


def _build_estimator(config: TrainConfig):
    """
    Devuelve un modelo según lo especificado en la configuración
    """

    if config.model.type == "random_forest":
        return RandomForestClassifier(
            n_estimators = config.model.n_estimators,
            max_depth = config.model.max_depth,
            random_state = config.run.seed
        )
    if config.model.type == "logistic_regression":
        return LogisticRegression(
            max_iter = config.model.max_iter,
            random_state = config.run.seed
        )
    raise ValueError(f"Modelo no soportado: {config.model.type!r}")


def build_pipeline(config: TrainConfig) -> Pipeline:
    """
    Construye el pipeline que incluye preprocesamiento + modelo

    Es el objeto que se serializa como artefacto. Icluye el preprocesamiento
    para que entrenamiento e inferencia compartan exactamente la misma lógica.
    """

    return Pipeline(
        steps = [
            ("preprocessor", _build_preprocessor(config)),
            ("estimator", _build_estimator(config))
        ]
    )

