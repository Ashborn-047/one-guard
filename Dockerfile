FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

# Install system dependencies needed for compiling C extensions (like psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY src/ ./src/
COPY dashboard/api.py ./dashboard/api.py
COPY dashboard/frontend/dist/ ./dashboard/frontend/dist/
COPY main.py .

# Create a non-root user to run the app
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

# Expose FastAPI Dashboard port
EXPOSE 8000

# Start unified runner
CMD ["python", "main.py"]
