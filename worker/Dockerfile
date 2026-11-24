FROM node:25-bookworm-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370
ARG SOURCE_URL
ARG SOURCE_REVISION
LABEL org.opencontainers.image.source=$SOURCE_URL \
      org.opencontainers.image.revision=$SOURCE_REVISION \
      org.opencontainers.image.description="Isolated Headroom AgentOps harness worker"
RUN apt-get update && apt-get install -y --no-install-recommends git python3 python3-venv ca-certificates ripgrep build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY worker/package.json worker/package-lock.json /opt/harness/
RUN cd /opt/harness && npm ci --ignore-scripts --no-audit --no-fund \
    && node node_modules/@anthropic-ai/claude-code/install.cjs \
    && node_modules/.bin/claude --version \
    && npm cache clean --force
ENV PATH=/opt/harness/node_modules/.bin:$PATH
COPY agentops /opt/app/agentops
COPY worker/entry.py /opt/app/entry.py
ENV PYTHONPATH=/opt/app
USER node
WORKDIR /workspace
ENTRYPOINT ["python3", "/opt/app/entry.py"]
