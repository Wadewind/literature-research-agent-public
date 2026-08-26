FROM opensandbox/chrome@sha256:507a6c9075c6c588288ce64cab63d43b0b2ef2011e1da2c02b55fdec4c50cc58

USER root

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        python3 \
        python3-pip \
        python3-venv \
    && python3 -m venv /opt/research-agent-venv \
    && /opt/research-agent-venv/bin/pip install --no-cache-dir \
        matplotlib==3.10.6 \
        numpy==2.3.3 \
        pandas==2.3.2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/research-agent-venv/bin:${PATH}" \
    HOME="/home/chrome" \
    MPLCONFIGDIR="/workspace/.matplotlib"

RUN mkdir -p /workspace /home/chrome/.cache \
    && chown -R chrome:chrome /workspace /home/chrome /opt/research-agent-venv

USER chrome
WORKDIR /workspace
