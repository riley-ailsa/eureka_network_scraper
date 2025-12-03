# Eureka Network Scraper

Scrapes funding opportunities from Eureka Network for Ailsa grant discovery platform.

## Project Structure

```
src/          - Source code (scraper, ingestion)
scripts/      - Utility scripts (testing, exports)
config/       - Database schemas and setup
outputs/      - Generated files (Excel, logs)
data/         - Scraped data storage
docs/         - Documentation
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your MongoDB URI

# Run scraper
python run_scraper.py

# Export to Excel for review
python scripts/export_to_excel.py
```

## Deployment

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for production deployment instructions.

---

## Detailed Documentation

### Features

- **Comprehensive scraping** - All open, closed, and upcoming grants
- **Structured content extraction** - About, eligibility, funding by country, application instructions, key dates
- **Smart pagination** - Handles multi-page listings automatically
- **Rich embeddings** - Uses all structured sections for semantic search
- **Automated ingestion** - MongoDB + Pinecone with proper error handling
- **Cron scheduling** - Set it and forget it with automated runs
- **Detailed logging** - Track success/failures with timestamped logs and JSON summaries

### Installation

```bash
# Clone or navigate to this directory
cd eureka_network_scraper

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.template .env
# Edit .env with your credentials:
# - OPENAI_API_KEY
# - PINECONE_API_KEY
# - PINECONE_INDEX_NAME (default: ailsa-grants)
# - MONGO_URI (MongoDB connection string)
# - MONGO_DB_NAME (default: ailsa_grants)
```

### Test Connections

```bash
python scripts/test_connections.py
```

Make sure all three connections pass (OpenAI, Pinecone, MongoDB)

### Run Scraper + Ingestion

**Option A: Simple run**

```bash
python run_scraper.py
```

**Option B: Automated cron run (with logging)**

```bash
python cron_job.py
```

**Option C: Set up cron job (fully automated)**

```bash
chmod +x scripts/setup_cron.sh
./scripts/setup_cron.sh
# Choose schedule: daily, weekly, monthly, or custom
```

## What Gets Scraped

### Grant Data (40 total grants)
- **29 traditional R&D grants** - Network Projects, Eurostars, Innowwide, Globalstars, Clusters
- **11 Investment Readiness opportunities** - Corporate Online Sessions and Corporate Challenges

### Extracted Fields
- Title, URL, programme, status
- Open date and close date (100% coverage)
- Structured sections:
  - About/Description
  - Eligibility criteria
  - Funding information (by country)
  - How to apply
  - Key dates
  - Country-specific requirements

### Output Format

```json
[
  {
    "id": "eureka_network:call-slug",
    "source": "eureka_network",
    "title": "Call Title",
    "url": "https://www.eurekanetwork.org/...",
    "status": "Open|Closed|Unknown",
    "programme": "Network Projects|Eurostars|Innowwide|...",
    "call_id": "call-slug",
    "open_date": "2025-07-01T00:00:00",
    "close_date": "2025-11-21T00:00:00",
    "is_supplemental": false,
    "raw": {
      "description": "...",
      "funding_info": "...",
      "sections": {
        "about": "...",
        "eligibility": "...",
        "funding": {"Country": "..."},
        "how_to_apply": "...",
        "key_dates": "...",
        "country_info": {}
      }
    }
  }
]
```

**Note:** `is_supplemental: true` for Investment Readiness opportunities, `false` for traditional R&D grants.

## MongoDB Document Schema

Grants are stored in MongoDB with the following schema:

```javascript
{
  // Primary identifiers
  "grant_id": "eureka_call-slug",     // Unique ID prefixed with "eureka_"
  "source": "eureka",                  // Always "eureka"
  "external_id": "call-slug",          // Original call identifier

  // Core metadata
  "title": "Call Title",
  "url": "https://www.eurekanetwork.org/...",
  "description": "Full description text...",

  // Status & dates
  "status": "open",                    // open/closed/upcoming/unknown
  "is_active": true,                   // true if status == "open"
  "opens_at": ISODate("2025-07-01"),
  "closes_at": ISODate("2025-11-21"),

  // Funding
  "total_fund_gbp": null,              // Eureka often doesn't specify total pot
  "total_fund_display": "Funding info text",
  "project_funding_min": null,
  "project_funding_max": null,
  "competition_type": "grant",

  // Programme info
  "programme": "Eurostars",            // e.g., Eurostars, GlobalStars, Network Projects

  // Classification
  "tags": ["eureka", "eurostars"],
  "sectors": ["technology", "healthcare"],

  // Raw data (preserved for re-parsing)
  "raw": {
    "description": "...",
    "funding_info": "...",
    "sections": {...},
    "is_supplemental": false,
    "original_id": "eureka_network:call-slug",
    "original_source": "eureka_network"
  },

  // Timestamps
  "scraped_at": ISODate("2025-11-20T10:00:00Z"),
  "updated_at": ISODate("2025-11-20T10:00:00Z"),
  "created_at": ISODate("2025-11-20T10:00:00Z")
}
```

### Pinecone (`ailsa-grants` index)
- Vector embedding from all structured sections
- Metadata: `source`, `title`, `url`, `status`, `programme`, `opens_at`, `closes_at`, `is_active`

## Ingestion Options

When running `ingest_eureka_only.py`, you can choose:

1. **All grants (40 total)** - Everything including Investment Readiness
2. **Primary R&D grants only (29 grants)** - Excludes Investment Readiness
3. **Supplemental opportunities only (11 grants)** - Only Investment Readiness

Or filter programmatically:

```python
import json

data = json.load(open('data/eureka_network/normalized.json'))
primary_grants = [g for g in data if not g.get('is_supplemental', False)]
```

## Automated Scheduling (Cron)

### Setup

```bash
./scripts/setup_cron.sh
```

Choose your schedule:
- **Daily at 2:00 AM** (recommended)
- **Every Monday at 3:00 AM** (weekly)
- **First day of month at 2:00 AM** (monthly)
- **Every 6 hours** (frequent updates)
- **Custom cron expression**

### What Happens During Each Run

```
1. Scraping Phase
   - Fetches all grants from Eureka Network
   - Extracts structured content
   - Saves to data/eureka_network/normalized.json

2. Ingestion Phase
   - Normalizes grants to MongoDB schema
   - Upserts documents to MongoDB
   - Generates OpenAI embeddings
   - Indexes embeddings in Pinecone

3. Logging & Monitoring
   - Detailed log: outputs/logs/cron_YYYYMMDD_HHMMSS.log
   - Run summary: outputs/logs/summary_YYYYMMDD_HHMMSS.json
   - Latest run: outputs/logs/latest_run.json
```

**Typical runtime:** 3-7 minutes per run

### Monitoring

```bash
# Check latest run status
cat outputs/logs/latest_run.json

# View live logs
tail -f outputs/logs/cron_output.log

# List installed cron jobs
crontab -l
```

Example summary output:

```json
{
  "timestamp": "2025-11-20T15:30:00",
  "scraping": {
    "grants_found": 40
  },
  "ingestion": {
    "total": 40,
    "mongo_success": 40,
    "pinecone_success": 40,
    "failed": 0
  },
  "elapsed_seconds": 125.3,
  "log_file": "outputs/logs/cron_20251120_153000.log"
}
```

### Exit Codes

- `0` - Success (all grants ingested)
- `1` - Fatal error (scraping failed, connection issues)
- `2` - Partial success (some grants failed)

### Uninstalling Cron

```bash
crontab -e
# Delete lines marked "# Eureka Network Scraper"
```

## Files & Scripts

| File | Purpose |
|------|---------|
| `run_scraper.py` | Main entry point - scrapes and ingests |
| `cron_job.py` | Automated scraper + ingestion for cron |
| `src/scraper.py` | Core scraper - extracts all grant data |
| `src/ingest.py` | MongoDB + Pinecone ingestion |
| `scripts/setup_cron.sh` | Interactive cron job installer |
| `scripts/test_connections.py` | Test OpenAI, Pinecone, MongoDB connections |
| `scripts/export_to_excel.py` | Export grants to Excel for review |
| `config/mongo_setup.js` | MongoDB setup script |
| `requirements.txt` | Python dependencies |

## Configuration

### Environment Variables (`.env`)

```bash
OPENAI_API_KEY=sk-...
PINECONE_API_KEY=...
PINECONE_INDEX_NAME=ailsa-grants
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/
MONGO_DB_NAME=ailsa_grants
```

### Customize Ingestion

Edit `cron_job.py` line 253 to change what gets ingested:

```python
# Ingest all grants (default)
ingestion_results = run_ingestion(grants, ingest_all=True)

# OR ingest only primary R&D grants
ingestion_results = run_ingestion(grants, ingest_all=False)
```

## Testing

```bash
# Run scraper + ingest
python run_scraper.py

# Verify
mongosh $MONGO_URI --eval 'use ailsa_grants; db.grants.countDocuments({source: "eureka"})'
```

## Troubleshooting

### Connection Errors
- Check `.env` file has correct credentials
- Verify MongoDB is accessible (check IP whitelist if using Atlas)
- Confirm Pinecone index exists

### Missing Data
- Some grants may show "unknown" status if dates couldn't be parsed
- Grants will still be ingested with available data

### Cron Job Not Running

**macOS:**
```bash
# Grant Full Disk Access to cron
System Preferences > Security & Privacy > Full Disk Access
Add: /usr/sbin/cron or Terminal.app
```

**Linux:**
```bash
# Check cron service
systemctl status cron

# View cron logs
grep CRON /var/log/syslog
```

### Re-running Ingestion

Safe to run multiple times - uses upsert to update existing records.

## Advanced Usage

### Email Notifications

```bash
# In crontab
MAILTO=your-email@example.com
0 2 * * * cd /path/to/scraper && python3 run_scraper_cron.py
```

### Slack/Discord Webhooks

Add to `cron_job.py`:

```python
import requests

def send_slack_notification(summary):
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if webhook_url:
        requests.post(webhook_url, json={
            "text": f"Eureka scraper: {summary['ingestion']['mongo_success']} grants ingested"
        })
```

### Custom Filters

```python
# custom_run.py
from cron_job import run_scraper, run_ingestion

grants = run_scraper()

# Filter to only open grants
open_grants = [g for g in grants if g.get('status') == 'Open']

run_ingestion(open_grants)
```

## Statistics

**Last run:**
- 40 total grants
  - 9 open
  - 31 closed
  - 0 upcoming
- 100% date extraction coverage
- 100% MongoDB success
- 100% Pinecone indexing success

## Best Practices

1. **Start with weekly scheduling** - Grants don't update that frequently
2. **Monitor logs for first few weeks** - Ensure everything runs smoothly
3. **Adjust schedule based on updates** - Check how often Eureka Network changes
4. **Set up alerts** - Use email or webhooks for failures
5. **Keep logs for debugging** - Historical data helps troubleshoot issues

## License

MIT (or your license of choice)
