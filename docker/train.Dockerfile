ARG BASE_IMAGE=penguins/base:local
FROM ${BASE_IMAGE}
ENTRYPOINT ["python", "-m", "penguins_ml.train"]