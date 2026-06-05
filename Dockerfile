# Use lightweight official Python image
FROM python:3.12-slim

# Prevent Python from writing pyc files to disc and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies (needed for compiling some packages if applicable)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application files
COPY . /app/

# Create folders for uploads, database and logs
RUN mkdir -p /app/uploads /app/instance /app/logs

# Expose Port 5000
EXPOSE 5000

# Set environment variables defaults
ENV FLASK_APP=app.py
ENV FLASK_ENV=production

# Run with Gunicorn on startup
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--log-level", "info", "app:app"]
