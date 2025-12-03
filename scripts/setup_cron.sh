#!/bin/bash
# Setup script for Eureka Network scraper cron job

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo "=========================================="
echo "EUREKA NETWORK SCRAPER - CRON SETUP"
echo "=========================================="

# Get the absolute path to this script's directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo -e "${GREEN}Working directory: $SCRIPT_DIR${NC}"

# Check if .env file exists
if [ ! -f .env ]; then
    echo -e "${RED}Error: .env file not found!${NC}"
    echo "Please create .env from .env.template and add your credentials"
    exit 1
fi

# Make Python scripts executable
chmod +x run_scraper_cron.py
chmod +x eureka_scraper.py
echo -e "${GREEN}✓ Made scripts executable${NC}"

# Check Python dependencies
echo "Checking Python dependencies..."
python3 -c "import requests, bs4, dateutil, psycopg2, openai, pinecone, dotenv" 2>/dev/null
if [ $? -ne 0 ]; then
    echo -e "${YELLOW}Installing dependencies...${NC}"
    pip3 install -r requirements.txt
else
    echo -e "${GREEN}✓ All dependencies installed${NC}"
fi

# Create logs directory
mkdir -p logs
echo -e "${GREEN}✓ Created logs directory${NC}"

# Detect Python path
PYTHON_PATH=$(which python3)
echo -e "${GREEN}Python path: $PYTHON_PATH${NC}"

# Create cron command
CRON_CMD="$PYTHON_PATH $SCRIPT_DIR/run_scraper_cron.py >> $SCRIPT_DIR/logs/cron_output.log 2>&1"

echo ""
echo "=========================================="
echo "CRON SCHEDULE OPTIONS"
echo "=========================================="
echo "Select when you want to run the scraper:"
echo ""
echo "1) Daily at 2:00 AM"
echo "2) Every Monday at 3:00 AM (weekly)"
echo "3) First day of every month at 2:00 AM (monthly)"
echo "4) Every 6 hours"
echo "5) Custom schedule"
echo "6) Don't install cron (just test the script)"
echo ""
read -p "Enter choice (1-6): " choice

case $choice in
    1)
        CRON_SCHEDULE="0 2 * * *"
        SCHEDULE_DESC="Daily at 2:00 AM"
        ;;
    2)
        CRON_SCHEDULE="0 3 * * 1"
        SCHEDULE_DESC="Every Monday at 3:00 AM"
        ;;
    3)
        CRON_SCHEDULE="0 2 1 * *"
        SCHEDULE_DESC="First day of every month at 2:00 AM"
        ;;
    4)
        CRON_SCHEDULE="0 */6 * * *"
        SCHEDULE_DESC="Every 6 hours"
        ;;
    5)
        echo ""
        echo "Enter cron schedule (e.g., '0 2 * * *' for daily at 2 AM):"
        echo "Format: minute hour day month weekday"
        read -p "Schedule: " CRON_SCHEDULE
        SCHEDULE_DESC="Custom: $CRON_SCHEDULE"
        ;;
    6)
        echo ""
        echo -e "${YELLOW}Skipping cron installation - testing script instead${NC}"
        echo ""
        echo "To run manually:"
        echo "  $PYTHON_PATH $SCRIPT_DIR/run_scraper_cron.py"
        echo ""
        echo "To test the script now, run:"
        echo "  $PYTHON_PATH $SCRIPT_DIR/run_scraper_cron.py"
        exit 0
        ;;
    *)
        echo -e "${RED}Invalid choice${NC}"
        exit 1
        ;;
esac

# Full cron line
CRON_LINE="$CRON_SCHEDULE cd $SCRIPT_DIR && $CRON_CMD"

echo ""
echo "=========================================="
echo "CRON JOB CONFIGURATION"
echo "=========================================="
echo "Schedule: $SCHEDULE_DESC"
echo "Command: $CRON_CMD"
echo ""
echo "This cron job will:"
echo "  1. Scrape all Eureka Network grants"
echo "  2. Ingest to PostgreSQL + Pinecone"
echo "  3. Log results to logs/ directory"
echo ""
read -p "Install this cron job? (y/n): " confirm

if [ "$confirm" != "y" ]; then
    echo "Cancelled"
    exit 0
fi

# Check if cron job already exists
CRON_MARKER="# Eureka Network Scraper"
if crontab -l 2>/dev/null | grep -q "$CRON_MARKER"; then
    echo ""
    echo -e "${YELLOW}Existing cron job found. Removing old job...${NC}"
    crontab -l 2>/dev/null | grep -v "$CRON_MARKER" | grep -v "run_scraper_cron.py" | crontab -
fi

# Add new cron job
(crontab -l 2>/dev/null; echo ""; echo "$CRON_MARKER"; echo "$CRON_LINE") | crontab -

echo ""
echo -e "${GREEN}=========================================="
echo "✓ CRON JOB INSTALLED SUCCESSFULLY"
echo "==========================================${NC}"
echo ""
echo "Schedule: $SCHEDULE_DESC"
echo "Log files: $SCRIPT_DIR/logs/"
echo ""
echo "To view current cron jobs:"
echo "  crontab -l"
echo ""
echo "To remove this cron job:"
echo "  crontab -e"
echo "  (then delete the lines marked 'Eureka Network Scraper')"
echo ""
echo "To test the script manually:"
echo "  $PYTHON_PATH $SCRIPT_DIR/run_scraper_cron.py"
echo ""
echo "To view the latest run summary:"
echo "  cat $SCRIPT_DIR/logs/latest_run.json"
echo ""
echo -e "${GREEN}Setup complete!${NC}"
