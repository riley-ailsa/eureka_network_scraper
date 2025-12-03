# Eureka Network Scraper - Docker Image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY config/ ./config/
COPY cron_job.py .
COPY run_scraper.py .

# Create necessary directories
RUN mkdir -p data/eureka_network outputs/logs outputs/excel

# Make scripts executable
RUN chmod +x cron_job.py run_scraper.py scripts/setup_cron.sh

# Set environment variables (will be overridden by docker-compose or runtime)
ENV PYTHONUNBUFFERED=1

# Default command - run the scraper once
CMD ["python3", "cron_job.py"]
