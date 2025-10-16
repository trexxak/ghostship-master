FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
  PYTHONUNBUFFERED=1

WORKDIR /app

# Install system deps for common Python packages (keep minimal)
RUN apt-get update \
  && apt-get install -y --no-install-recommends build-essential gcc libpq-dev \
  && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel \
  && pip install --no-cache-dir -r /app/requirements.txt

# Copy project
COPY . /app

# Non-root user for safety
RUN useradd --create-home appuser || true
USER appuser

EXPOSE 8000

# Entrypoint script handles migrations/initial tasks then runs the CMD
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "scripts/dev_bootstrap_and_run.py"]
