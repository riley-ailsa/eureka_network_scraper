# Eureka Network Scraper

Automated scraper and ingestion pipeline for Eureka Network funding opportunities.

## Overview

Scrapes grant data from the Eureka Network website (https://www.eurekanetwork.org/programmes-and-calls/) and ingests it into PostgreSQL + Pinecone with OpenAI embeddings. Includes automated scheduling via cron jobs.

## Features

- **Comprehensive scraping** - All open, closed, and upcoming grants
- **Structured content extraction** - About, eligibility, funding by country, application instructions, key dates
- **Smart pagination** - Handles multi-page listings automatically
- **Rich embeddings** - Uses all structured sections for semantic search
- **Automated ingestion** - PostgreSQL + Pinecone with proper error handling
- **Cron scheduling** - Set it and forget it with automated runs
- **Detailed logging** - Track success/failures with timestamped logs and JSON summaries

## Quick Start

### 1. Installation

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
# - DATABASE_URL (PostgreSQL connection string)
```

### 2. Test Connections

```bash
python test_connections.py
```

Make sure all three connections pass ✅ (OpenAI, Pinecone, PostgreSQL)

### 3. Run Scraper + Ingestion

**Option A: Manual run (interactive)**

```bash
# Step 1: Scrape grants
python eureka_scraper.py

# Step 2: Ingest to databases (you'll choose what to ingest)
python ingest_eureka_only.py
```

**Option B: Automated run (non-interactive)**

```bash
# Scrape + ingest everything automatically
python run_scraper_cron.py
```

**Option C: Set up cron job (fully automated)**

```bash
chmod +x setup_cron.sh
./setup_cron.sh
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

## Database Schema

### PostgreSQL (`grants` table)
- `grant_id` - Primary key (e.g., `eureka_network:call-slug`)
- `source` - Always "eureka_network"
- `title` - Grant title
- `url` - Grant detail page URL
- `call_id` - Call identifier
- `status` - Open/Closed/Unknown
- `programme` - Programme name
- `open_date`, `close_date` - Dates
- `description_summary` - First 500 chars of description
- `scraped_at`, `updated_at` - Timestamps

### Pinecone (`ailsa-grants` index)
- Vector embedding from all structured sections
- Metadata: `source`, `title`, `url`, `status`, `programme`, `open_date`, `close_date`, `is_supplemental`

See [schema.sql](schema.sql) for full database schema.

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
./setup_cron.sh
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
   ├─ Fetches all grants from Eureka Network
   ├─ Extracts structured content
   └─ Saves to data/eureka_network/normalized.json

2. Ingestion Phase
   ├─ Generates OpenAI embeddings
   ├─ Inserts/updates grants in PostgreSQL
   └─ Indexes embeddings in Pinecone

3. Logging & Monitoring
   ├─ Detailed log: logs/cron_YYYYMMDD_HHMMSS.log
   ├─ Run summary: logs/summary_YYYYMMDD_HHMMSS.json
   └─ Latest run: logs/latest_run.json
```

**Typical runtime:** 3-7 minutes per run

### Monitoring

```bash
# Check latest run status
cat logs/latest_run.json

# View live logs
tail -f logs/cron_output.log

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
    "postgres_success": 40,
    "pinecone_success": 40,
    "failed": 0
  },
  "elapsed_seconds": 125.3,
  "log_file": "logs/cron_20251120_153000.log"
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
| `eureka_scraper.py` | Main scraper - extracts all grant data |
| `ingest_eureka_only.py` | Ingestion script (interactive) |
| `run_scraper_cron.py` | Automated scraper + ingestion for cron |
| `setup_cron.sh` | Interactive cron job installer |
| `test_connections.py` | Test OpenAI, Pinecone, PostgreSQL connections |
| `schema.sql` | PostgreSQL database schema |
| `requirements.txt` | Python dependencies |
| `.env.template` | Environment variables template |

## Configuration

### Environment Variables (`.env`)

```bash
OPENAI_API_KEY=sk-...
PINECONE_API_KEY=...
PINECONE_INDEX_NAME=ailsa-grants
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

### Customize Ingestion

Edit `run_scraper_cron.py` line 174 to change what gets ingested:

```python
# Ingest all grants (default)
ingestion_results = run_ingestion(grants, ingest_all=True)

# OR ingest only primary R&D grants
ingestion_results = run_ingestion(grants, ingest_all=False)
```

## Troubleshooting

### Connection Errors
- Check `.env` file has correct credentials
- Verify PostgreSQL is running and accessible
- Confirm Pinecone index exists

### Missing Data
- Some grants may show "Unknown" status if dates couldn't be parsed
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

Safe to run multiple times - uses `ON CONFLICT` to update existing records.

## Advanced Usage

### Email Notifications

```bash
# In crontab
MAILTO=your-email@example.com
0 2 * * * cd /path/to/scraper && python3 run_scraper_cron.py
```

### Slack/Discord Webhooks

Add to `run_scraper_cron.py`:

```python
import requests

def send_slack_notification(summary):
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if webhook_url:
        requests.post(webhook_url, json={
            "text": f"Eureka scraper: {summary['ingestion']['postgres_success']} grants ingested"
        })
```

### Custom Filters

```python
# custom_run.py
from run_scraper_cron import run_scraper, run_ingestion

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
- 100% PostgreSQL success
- 100% Pinecone indexing success

## Best Practices

1. **Start with weekly scheduling** - Grants don't update that frequently
2. **Monitor logs for first few weeks** - Ensure everything runs smoothly
3. **Adjust schedule based on updates** - Check how often Eureka Network changes
4. **Set up alerts** - Use email or webhooks for failures
5. **Keep logs for debugging** - Historical data helps troubleshoot issues

## License

MIT (or your license of choice)
