ARG BASE_IMAGE=penguins/base:local
FROM ${BASE_IMAGE}

USER appuser
RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=1000 \
    uv sync --locked --extra mlflow --no-editable

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 5000
ENTRYPOINT ["mlflow", "server", \
            "--host=0.0.0.0", \
            "--port=5000", \
            "--workers=1", \
            "--allowed-hosts=localhost,localhost:5000,127.0.0.1, 127.0.0.1:5000,mlflow,mlflow:5000", \
            "--backend-store-uri=sqlite:////srv/pinguinos/mlflow/mlflow.db", \
            "--artifacts-destination=/srv/pinguinos/mlflow/artifacts"]