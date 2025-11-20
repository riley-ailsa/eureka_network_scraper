# Deployment Guide

## Deployment Options

You have three deployment options:

### Option 1: Direct Deployment (Current Setup)
**Best for:** VPS, EC2, dedicated server

Already configured! Just use the cron setup:
```bash
./setup_cron.sh
```

**Pros:** Simple, direct, uses system cron
**Cons:** Requires server access, less portable

---

### Option 2: Docker (Recommended for Cloud)
**Best for:** Cloud platforms, containers, Kubernetes

#### Build and Run

```bash
# Build the image
docker build -t eureka-scraper .

# Run once (manual)
docker run --env-file .env \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  eureka-scraper

# Or use docker-compose
docker-compose up -d
```

#### Run on Schedule with Docker

**Option A: Docker + Host Cron**
```bash
# Add to your crontab:
0 2 * * * docker run --env-file /path/to/.env -v /path/to/data:/app/data -v /path/to/logs:/app/logs eureka-scraper
```

**Option B: Docker with Loop**
Edit `docker-compose.yml` and uncomment the command section:
```yaml
command: >
  sh -c "
  while true; do
    python3 run_scraper_cron.py;
    sleep 86400;  # 24 hours
  done
  "
```

Then run:
```bash
docker-compose up -d
```

**Pros:** Portable, isolated, works everywhere
**Cons:** Extra layer, slightly more complex

---

### Option 3: Cloud Scheduled Jobs
**Best for:** Serverless, managed services

#### AWS Lambda + EventBridge
```bash
# Package for Lambda
pip install -r requirements.txt -t lambda_package/
cp eureka_scraper.py ingest_eureka_only.py lambda_package/
cd lambda_package && zip -r ../lambda.zip .
```

Set environment variables in Lambda console, schedule with EventBridge.

#### Google Cloud Run + Cloud Scheduler
```bash
# Deploy to Cloud Run
gcloud run deploy eureka-scraper \
  --source . \
  --set-env-vars OPENAI_API_KEY=$OPENAI_API_KEY

# Schedule with Cloud Scheduler
gcloud scheduler jobs create http eureka-scraper-daily \
  --schedule="0 2 * * *" \
  --uri="https://your-cloud-run-url"
```

#### Azure Container Instances + Logic Apps
Similar approach - deploy container, schedule with Logic Apps.

**Pros:** Fully managed, auto-scaling, no server maintenance
**Cons:** Cold starts, requires cloud account, potentially higher cost

---

## Monitoring in Production

### Health Checks

Add to your monitoring system:

```bash
# Check last run status
curl -f http://your-server/logs/latest_run.json || alert

# Or check locally
[ $(jq '.ingestion.failed' logs/latest_run.json) -eq 0 ] || alert
```

### Alerting

**Slack webhook** (add to `run_scraper_cron.py`):
```python
import requests
def send_alert(message):
    requests.post(os.getenv("SLACK_WEBHOOK_URL"),
                  json={"text": message})
```

**Email** (via cron MAILTO):
```bash
MAILTO=your-email@example.com
0 2 * * * cd /path && python3 run_scraper_cron.py
```

**Monitoring services:**
- Cronitor.io
- Healthchecks.io
- UptimeRobot

---

## Environment Variables for Production

Make sure these are set in production:

```bash
OPENAI_API_KEY=sk-...
PINECONE_API_KEY=...
PINECONE_INDEX_NAME=ailsa-grants
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Optional
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
```

---

## Security Checklist

- [ ] `.env` file NOT committed to git
- [ ] Database uses SSL connection
- [ ] API keys rotated regularly
- [ ] Logs don't contain sensitive data
- [ ] Container runs as non-root user (if using Docker)
- [ ] Network access restricted (firewall rules)

---

## Backup & Recovery

### Backup Strategy

```bash
# Backup PostgreSQL
pg_dump $DATABASE_URL > backups/grants_$(date +%Y%m%d).sql

# Backup Pinecone vectors (if needed)
# Pinecone has built-in backups, but you can export metadata
```

### Recovery

```bash
# Restore PostgreSQL
psql $DATABASE_URL < backups/grants_20251120.sql

# Re-run scraper to rebuild Pinecone
python3 run_scraper_cron.py
```

---

## Scaling Considerations

Current setup handles 40 grants easily. If scaling:

1. **More grants (100+):** Current setup fine, may need batch processing
2. **Multiple sources:** Run separate containers/cron jobs per source
3. **High frequency (hourly):** Consider rate limiting on OpenAI API
4. **Large embeddings:** Monitor Pinecone quota and costs

---

## Cost Estimates

**Current usage (weekly runs):**
- OpenAI embeddings: 40 grants × $0.0001/1K tokens ≈ $0.10/month
- Pinecone: Depends on plan, starter plan sufficient
- PostgreSQL: Minimal (few KB per grant)
- Server/hosting: Depends on platform

**Daily runs:** Multiply by ~4

---

## Recommended Production Setup

**For most use cases:**
```
Option 1 (Direct + Cron) on a small VPS
- DigitalOcean Droplet ($6/month)
- AWS EC2 t2.micro (free tier)
- Google Cloud e2-micro (free tier)
```

**For enterprise:**
```
Option 2 (Docker) + Kubernetes
- Scalable
- High availability
- Easy rollbacks
```

**For serverless:**
```
Option 3 (Cloud Functions)
- AWS Lambda + EventBridge
- Pay only when running
- Zero maintenance
```

---

## Quick Deploy Commands

### VPS/Server (Current Setup)
```bash
git clone <repo>
cd eureka_network_scraper
pip install -r requirements.txt
cp .env.template .env
# Edit .env
python test_connections.py
./setup_cron.sh
```

### Docker
```bash
git clone <repo>
cd eureka_network_scraper
cp .env.template .env
# Edit .env
docker-compose up -d
```

### AWS Lambda
```bash
# Package and deploy
pip install -r requirements.txt -t package/
cp *.py package/
cd package && zip -r ../lambda.zip . && cd ..
aws lambda create-function --function-name eureka-scraper \
  --runtime python3.11 --handler run_scraper_cron.main \
  --zip-file fileb://lambda.zip
```

---

Choose the option that best fits your infrastructure!
