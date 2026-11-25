# Resolve the built image to its immutable ID before running an experiment.
# Builders may override PYTHON_IMAGE with a registry digest for reproducible builds.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b
FROM ${PYTHON_IMAGE}
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /opt/headroom-agentops
COPY agentops/benchmarks ./agentops/benchmarks
RUN touch agentops/__init__.py
ENV PYTHONPATH=/opt/headroom-agentops
WORKDIR /work
ENTRYPOINT ["python", "-m", "agentops.benchmarks.cli"]
