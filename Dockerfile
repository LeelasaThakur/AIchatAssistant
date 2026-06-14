# ============================================================
# AI Chat Assistant — Production Dockerfile
# ============================================================
# Multi-stage build for minimal image size.
# Stage 1: install dependencies
# Stage 2: copy app into slim runtime image
# ============================================================

# --- Stage 1: Builder ---
FROM python:3.12-slim AS builder

WORKDIR /build

# Install system dependencies for psycopg2 and document parsing
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# --- Stage 2: Runtime ---
FROM python:3.12-slim

# Security: run as non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Install runtime-only system libs
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy application code
COPY app.py config.py extensions.py models.py document_parser.py requirements.txt ./
COPY templates/ templates/

# Create writable directories
RUN mkdir -p uploads instance logs && \
    chown -R appuser:appuser /app

USER appuser

# Environment defaults (override via --env-file or -e)
ENV FLASK_ENV=production \
    SESSION_COOKIE_SECURE=true \
    PYTHONUNBUFFERED=1

EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Gunicorn with production-tuned settings
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "4", \
     "--threads", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]