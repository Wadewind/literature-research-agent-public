FROM node:24.19.0-trixie-slim@sha256:ab3eebe934147fee049b5eb83c570f68c849a13c930bdfa482de99fcdfa3b3de AS playwright-mcp

ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

WORKDIR /opt/research-agent-mcp-node
COPY mcp-node/package.json mcp-node/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts \
    && npm cache clean --force

FROM opensandbox/chrome@sha256:507a6c9075c6c588288ce64cab63d43b0b2ef2011e1da2c02b55fdec4c50cc58

USER root
COPY requirements-mcp.lock /tmp/requirements-mcp.lock

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        python3 \
        python3-pip \
        python3-venv \
    && python3 -m venv /opt/research-agent-venv \
    && /opt/research-agent-venv/bin/pip install --no-cache-dir \
        --require-hashes \
        -r /tmp/requirements-mcp.lock \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=playwright-mcp /usr/local/bin/node /usr/local/bin/node
COPY --from=playwright-mcp \
    /opt/research-agent-mcp-node/node_modules \
    /opt/research-agent-mcp-node/node_modules
COPY --chmod=0755 start-mcp-service /opt/research-agent/start-mcp-service
COPY --chmod=0755 start-browser-proxy /opt/research-agent/start-browser-proxy
# Base entrypoint 直接按 PATH 启动 Xtigervnc，不读取 vncserver 用户配置。
# 固定 wrapper 只在 Sandbox namespace loopback 提供免密码 RFB；外层仍由平台 ticket gateway 鉴权。
COPY --chmod=0755 start-vnc-server /opt/research-agent-venv/bin/Xtigervnc

ENV PATH="/opt/research-agent-venv/bin:${PATH}" \
    HOME="/home/chrome" \
    MPLCONFIGDIR="/workspace/.matplotlib"

RUN mkdir -p /workspace/outputs /home/chrome/.cache \
    && chown -R chrome:chrome /workspace /home/chrome /opt/research-agent-venv

USER chrome
WORKDIR /workspace
