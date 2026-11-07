ARG BASE_IMAGE=penguins/base:local
FROM ${BASE_IMAGE}

USER appuser
RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=1000 \
    uv sync --locked --extra dev --extra jupyter --no-editable

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8888
ENTRYPOINT ["jupyter", "lab", "--ip=0.0.0.0", "--no-browser", \
            "--ServerApp.port=8888", "--ServerApp.token=", "--ServerApp.password="]