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
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/
MONGO_DB_NAME=ailsa_grants

# Optional
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
```

---

## MongoDB Setup

### MongoDB Atlas (Recommended for Production)

1. Create a free cluster at [MongoDB Atlas](https://www.mongodb.com/cloud/atlas)
2. Create a database user with read/write permissions
3. Whitelist your server's IP address (or use 0.0.0.0/0 for development)
4. Get the connection string and set as `MONGO_URI`

```bash
# Example MongoDB Atlas connection string
MONGO_URI=mongodb+srv://username:password@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
```

### Local MongoDB (Development)

```bash
# Install MongoDB
brew install mongodb-community  # macOS
# or
sudo apt install mongodb        # Ubuntu

# Start MongoDB
brew services start mongodb-community  # macOS
sudo systemctl start mongodb           # Linux

# Connection string for local MongoDB
MONGO_URI=mongodb://localhost:27017
```

### Create Index for Performance

```javascript
// Connect to MongoDB and create index on grant_id
use ailsa_grants;
db.grants.createIndex({ "grant_id": 1 }, { unique: true });
db.grants.createIndex({ "source": 1 });
db.grants.createIndex({ "status": 1 });
db.grants.createIndex({ "closes_at": 1 });
```

---

## Security Checklist

- [ ] `.env` file NOT committed to git
- [ ] MongoDB uses authentication
- [ ] MongoDB connection uses TLS/SSL (Atlas does this by default)
- [ ] API keys rotated regularly
- [ ] Logs don't contain sensitive data
- [ ] Container runs as non-root user (if using Docker)
- [ ] Network access restricted (firewall rules)
- [ ] MongoDB IP whitelist configured (Atlas)

---

## Backup & Recovery

### Backup Strategy

```bash
# Backup MongoDB (Atlas has automated backups)
# For self-hosted MongoDB:
mongodump --uri="$MONGO_URI" --out=backups/$(date +%Y%m%d)

# Or export specific collection
mongoexport --uri="$MONGO_URI" --db=ailsa_grants --collection=grants --out=backups/grants_$(date +%Y%m%d).json
```

### Recovery

```bash
# Restore MongoDB
mongorestore --uri="$MONGO_URI" backups/20251120/

# Or import specific collection
mongoimport --uri="$MONGO_URI" --db=ailsa_grants --collection=grants --file=backups/grants_20251120.json

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

### MongoDB Scaling

- **Atlas M0 (Free):** Good for development, 512MB storage
- **Atlas M10+:** Production workloads, dedicated resources
- **Sharding:** For very large datasets (millions of documents)

---

## Cost Estimates

**Current usage (weekly runs):**
- OpenAI embeddings: 40 grants × $0.0001/1K tokens ≈ $0.10/month
- Pinecone: Depends on plan, starter plan sufficient
- MongoDB Atlas: Free tier (M0) or ~$9/month (M10)
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

+ MongoDB Atlas M0 (free) or M10 ($9/month)
```

**For enterprise:**
```
Option 2 (Docker) + Kubernetes
- Scalable
- High availability
- Easy rollbacks

+ MongoDB Atlas M30+ (dedicated cluster)
```

**For serverless:**
```
Option 3 (Cloud Functions)
- AWS Lambda + EventBridge
- Pay only when running
- Zero maintenance

+ MongoDB Atlas Serverless
```

---

## Quick Deploy Commands

### VPS/Server (Current Setup)
```bash
git clone <repo>
cd eureka_network_scraper
pip install -r requirements.txt
cp .env.template .env
# Edit .env with MongoDB URI and other credentials
python test_connections.py
./setup_cron.sh
```

### Docker
```bash
git clone <repo>
cd eureka_network_scraper
cp .env.template .env
# Edit .env with MongoDB URI and other credentials
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

## Verifying Deployment

After deployment, verify the system is working:

```bash
# 1. Test connections
python test_connections.py

# 2. Run a manual scrape
python run_scraper_cron.py

# 3. Check MongoDB
mongosh "$MONGO_URI" --eval 'use ailsa_grants; db.grants.countDocuments({source: "eureka"})'

# 4. Check logs
cat logs/latest_run.json
```

Expected output:
```json
{
  "timestamp": "2025-11-20T15:30:00",
  "scraping": { "grants_found": 40 },
  "ingestion": {
    "total": 40,
    "mongo_success": 40,
    "pinecone_success": 40,
    "failed": 0
  }
}
```

---

Choose the option that best fits your infrastructure!
