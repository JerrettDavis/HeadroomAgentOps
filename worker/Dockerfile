FROM node:24-bookworm-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9
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
ARG DEVIN_VERSION=3000.13.4
ARG DEVIN_SHA256=e1bd618ec61f4d21069335162cf6d854d8cd2e662f2395efb06edb5796ed16e9
RUN python3 -c "import urllib.request; urllib.request.urlretrieve(\
    'https://static.devin.ai/cli/${DEVIN_VERSION}/devin-${DEVIN_VERSION}-x86_64-unknown-linux.tar.gz',\
    '/tmp/devin.tar.gz')" \
    && echo "${DEVIN_SHA256}  /tmp/devin.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/devin && tar xzf /tmp/devin.tar.gz -C /opt/devin \
    && ln -s /opt/devin/bin/devin /usr/local/bin/devin \
    && devin version && rm /tmp/devin.tar.gz
ENV PATH=/opt/harness/node_modules/.bin:$PATH
COPY agentops /opt/app/agentops
COPY worker/entry.py /opt/app/entry.py
ENV PYTHONPATH=/opt/app
USER node
WORKDIR /workspace
ENTRYPOINT ["python3", "/opt/app/entry.py"]
