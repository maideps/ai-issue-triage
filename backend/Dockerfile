# Backend image: API, CLI and the one-shot demo seeder share it.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Slow or flaky networks: a dropped index fetch can surface as a fake resolution conflict.
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    # CPU-only PyTorch: the default Linux wheel bundles CUDA (gigabytes).
    PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu \
    HF_HOME=/opt/models

WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/src ./src
RUN pip install .

# Bake the local embedding model in so the stack runs offline afterwards (D-007, D-008).
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY samples /app/samples
COPY evals /app/evals

ENV HF_HUB_OFFLINE=1 \
    ISSUE_TRIAGE_DATA_DIR=/app/data \
    ISSUE_TRIAGE_SAMPLES_DIR=/app/samples \
    ISSUE_TRIAGE_EVALS_DIR=/app/evals

EXPOSE 8000
CMD ["uvicorn", "issue_triage.main:app", "--host", "0.0.0.0", "--port", "8000"]
