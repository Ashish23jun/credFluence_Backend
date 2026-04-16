FROM python:3.12-slim AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# ------- deps stage -------
FROM base AS deps

COPY pyproject.toml .
RUN uv pip install --system -e ".[dev]"

# ------- final stage -------
FROM deps AS final

COPY . .

EXPOSE 8000

CMD ["gunicorn", "app.main:app", "-c", "gunicorn.conf.py"]
