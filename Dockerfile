# Eureka Network Scraper - Docker Image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    cron \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY eureka_scraper.py .
COPY ingest_eureka_only.py .
COPY run_scraper_cron.py .
COPY test_connections.py .
COPY schema.sql .

# Create necessary directories
RUN mkdir -p data/eureka_network logs

# Make scripts executable
RUN chmod +x run_scraper_cron.py

# Set environment variables (will be overridden by docker-compose or runtime)
ENV PYTHONUNBUFFERED=1

# Default command - run the scraper once
CMD ["python3", "run_scraper_cron.py"]
