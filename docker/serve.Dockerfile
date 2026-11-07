ARG BASE_IMAGE=penguins/base:local
FROM ${BASE_IMAGE}

USER appuser
RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=1000 \
    uv sync --locked --extra serve --no-editable

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8501
ENTRYPOINT ["streamlit", "run", "src/penguins_ml/serve.py", \
            "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]