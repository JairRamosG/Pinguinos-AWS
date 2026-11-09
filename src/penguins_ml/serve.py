"""UI de inferencia local con Streamlit.

Se ejecuta con `streamlit run src/penguins_ml/serve.py`.
El modelo se carga UNA vez por sesión y nunca se reentrena ni se modifica.
"""

from __future__ import annotations

import logging
import sys

import pandas as pd
import streamlit as st

from penguins_ml.serving import ArtifactError, load_artifact, predict

log = logging.getLogger("penguins_ml.serve")

ISLANDS = ["Biscoe", "Dream", "Torgersen"]
SEXES = ["male", "female"]


def _model_uri_from_argv(argv: list[str]) -> str | None:
    """Soporta `streamlit run app.py -- --model-uri <ruta>` y `--model-uri=<ruta>`."""
    for i, arg in enumerate(argv):
        if arg == "--model-uri" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--model-uri="):
            return arg.split("=", 1)[1]
    return None


@st.cache_resource(show_spinner="Cargando modelo...")
def _load(model_uri: str | None):
    """cache_resource: una carga por proceso, no por interacción."""
    return load_artifact(model_uri)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("penguins_ml").setLevel(logging.INFO)

    st.set_page_config(page_title="Penguins ML", page_icon="🐧")

    try:
        artifact = _load(_model_uri_from_argv(sys.argv[1:]))
    except ArtifactError as exc:
        st.error(f"No se pudo cargar el modelo: {exc}")
        st.stop()

    st.title("Clasificador de pingüinos")
    st.caption(
        "Predicción con el artefacto entrenado. La inferencia no reentrena ni modifica el modelo"
    )

    with st.sidebar:
        st.header("Modelo")
        st.markdown(f"**run_id:** `{artifact.run_id}`")
        short_hash = artifact.config_hash.removeprefix("sha256:")[:12]
        st.markdown(f"**config_hash:** `{short_hash}…`")
        for name, values in artifact.metrics.items():
            st.markdown(f"**{name}:** {values['mean']:.4f} ± {values['std']:.4f}")

    with st.form("prediccion"):
        left, right = st.columns(2)
        with left:
            island = st.selectbox("Isla", ISLANDS)
            bill_length = st.number_input(
                "Longitud del pico (mm)", min_value=30.0, max_value=60.0,
                value=45.0, step=0.1,
            )
            bill_depth = st.number_input(
                "Profundidad del pico (mm)", min_value=13.0, max_value=22.0,
                value=17.0, step=0.1,
            )
        with right:
            sex = st.selectbox("Sexo", SEXES)
            flipper = st.number_input(
                "Longitud de la aleta (mm)", min_value=170.0, max_value=235.0,
                value=200.0, step=1.0,
            )
            mass = st.number_input(
                "Masa corporal (g)", min_value=2500.0, max_value=6500.0,
                value=4200.0, step=10.0,
            )
        submitted = st.form_submit_button("Predecir")

    if submitted:
        values = {
            "island": island,
            "sex": sex,
            "bill_length_mm": bill_length,
            "bill_depth_mm": bill_depth,
            "flipper_length_mm": flipper,
            "body_mass_g": mass,
        }
        try:
            species, probabilities = predict(artifact, values)
        except (ValueError, KeyError) as exc:
            st.error(f"No se pudo predecir: {exc}")
            st.stop()

        st.success(f"Especie: **{species}**")
        table = pd.DataFrame(
            {
                "especie": list(probabilities),
                "probabilidad": [round(p, 4) for p in probabilities.values()],
            }
        ).sort_values("probabilidad", ascending=False)
        st.dataframe(table, hide_index=True, width="stretch")
        st.bar_chart(table.set_index("especie"))


main()