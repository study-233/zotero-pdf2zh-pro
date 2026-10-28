# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app/server

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libxcb1 \
    && rm -rf /var/lib/apt/lists/*

COPY server/pyproject.toml server/uv.lock ./
ARG UV_INDEX_URL
ENV UV_INDEX_URL=${UV_INDEX_URL}
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY server/LICENSES ./LICENSES
COPY server/babeldoc ./babeldoc
COPY server/pdf2zh_pro ./pdf2zh_pro
COPY server/THIRD_PARTY_NOTICES.md server/README.md ./
COPY server/service_launcher.py server/runtime_storage.py server/diagnostic_contract.py server/diagnostic_evidence.py server/diagnostics.py server/task_runtime.py server/document_export.py server/server.py server/task_manager.py server/observability.py server/provider_models.py server/codex_client.py server/codex_discovery.py server/chatgpt_auth.py server/chatgpt_client.py server/chatgpt_routes.py server/chatgpt_usage.py server/glossary_manager.py server/glossary_catalog.py server/translation_memory.py server/text_translation.py server/selection_cache.py server/maintenance.py ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

EXPOSE 8890

CMD ["uv", "run", "--no-sync", "zotero-pdf2zh-pro", "--host", "0.0.0.0", "--port", "8890"]
