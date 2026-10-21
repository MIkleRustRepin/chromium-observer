FROM ubuntu:24.04

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl file git lsb-release python3 rsync sudo util-linux xz-utils \
    && rm -rf /var/lib/apt/lists/*

ARG CHROMIUM_REVISION
RUN [[ "$CHROMIUM_REVISION" =~ ^[0-9a-f]{40}$ ]] \
    && curl --fail --location --retry 3 \
      "https://chromium.googlesource.com/chromium/src/+/${CHROMIUM_REVISION}/build/install-build-deps.py?format=TEXT" \
      | base64 --decode > /tmp/install-build-deps.py \
    && python3 /tmp/install-build-deps.py --no-prompt --no-arm --no-chromeos-fonts \
    && rm -f /tmp/install-build-deps.py \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 --shell /bin/bash builder && mkdir /workspace
COPY entrypoint.sh build.sh /usr/local/lib/chromium-observer/
RUN chmod +x /usr/local/lib/chromium-observer/*.sh
WORKDIR /workspace
ENTRYPOINT ["/usr/local/lib/chromium-observer/entrypoint.sh"]
