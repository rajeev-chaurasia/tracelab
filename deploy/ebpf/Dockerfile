# A Linux environment for recordings that include eBPF. bpftrace comes from the
# distribution, built for the architecture the container runs on; the Python
# environment is created by uv inside the container, separate from any host
# environment in the mounted repository.
FROM debian:bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends bpftrace ca-certificates git \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/opt/uv-cache \
    UV_LINK_MODE=copy \
    UV_PYTHON_PREFERENCE=only-managed
WORKDIR /src
