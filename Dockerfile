FROM python:3.12-slim

WORKDIR /app

# Install deps first (better layer caching)
COPY pyproject.toml ./
COPY gameos ./gameos
RUN pip install --no-cache-dir -e .

# Data dir for SQLite fallback / logs (Postgres is used in compose)
RUN mkdir -p /app/data

# Default: run the engine. Compose overrides `command` per service.
CMD ["gameos", "run", "--mode", "interval", "--every", "10m"]
