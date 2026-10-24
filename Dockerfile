# Lookout API + static dashboard.
# Build from the repo root:  docker build -t lookout .
# The app resolves paths relative to the repo root, so the image keeps the layout
#   /srv/lookout/{backend,frontend,evals}
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 10001 lookout

WORKDIR /srv/lookout

# Dependencies first for better layer caching.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY --chown=lookout:lookout backend/ backend/
COPY --chown=lookout:lookout frontend/ frontend/
COPY --chown=lookout:lookout evals/ evals/

USER lookout
WORKDIR /srv/lookout/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
