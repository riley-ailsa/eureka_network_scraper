#!/usr/bin/env python3
"""
Discovery script for new Eureka Network funding opportunities.

This script fetches new opportunities from https://www.eurekanetwork.org/programmes-and-calls/
and compares them against existing grants in MongoDB to identify new ones.

Designed to run as a cron job on Tuesdays and Fridays at 2 AM:
    0 2 * * 2,5 /path/to/python /path/to/discover_new_opportunities.py

Features:
- Fetches all open and upcoming opportunities
- Compares against existing grants in MongoDB
- Reports new opportunities found
- Optionally ingests new opportunities automatically
- Sends summary to stdout (can be captured by cron for email notifications)
"""

import sys
import os
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Set
import argparse
import traceback

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from pymongo import MongoClient

from src.scraper import EurekaNetworkScraper
from src.ingest import (
    normalize_eureka_grant,
    extract_embedding_text,
    create_embedding,
    upsert_to_mongodb,
    upsert_to_pinecone
)

# Load environment
load_dotenv()

# Configuration
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "ailsa_grants")
LOG_DIR = Path(__file__).parent.parent / "outputs" / "logs"
DATA_DIR = Path(__file__).parent.parent / "data" / "eureka_network"

# Create directories
LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Setup logging
log_file = LOG_DIR / f"discovery_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def get_existing_grant_ids() -> Set[str]:
    """
    Get all existing Eureka grant IDs from MongoDB.

    Returns:
        Set of grant_id strings
    """
    try:
        client = MongoClient(MONGO_URI)
        db = client[MONGO_DB_NAME]

        # Get all eureka grant IDs
        existing = db.grants.find(
            {"source": "eureka"},
            {"grant_id": 1, "_id": 0}
        )

        grant_ids = {doc["grant_id"] for doc in existing}
        client.close()

        logger.info(f"Found {len(grant_ids)} existing Eureka grants in MongoDB")
        return grant_ids

    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        return set()


def get_existing_urls() -> Set[str]:
    """
    Get all existing Eureka grant URLs from MongoDB.

    Returns:
        Set of URL strings
    """
    try:
        client = MongoClient(MONGO_URI)
        db = client[MONGO_DB_NAME]

        # Get all eureka grant URLs
        existing = db.grants.find(
            {"source": "eureka"},
            {"url": 1, "_id": 0}
        )

        urls = {doc["url"] for doc in existing}
        client.close()

        logger.info(f"Found {len(urls)} existing Eureka grant URLs in MongoDB")
        return urls

    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        return set()


def discover_new_opportunities(scrape_all_statuses: bool = False) -> List[Dict[str, Any]]:
    """
    Scrape Eureka Network and identify new opportunities.

    Args:
        scrape_all_statuses: If True, scrape open, closed, and upcoming.
                            If False (default), only scrape open and upcoming.

    Returns:
        List of new grant dictionaries
    """
    logger.info("=" * 60)
    logger.info("EUREKA NETWORK OPPORTUNITY DISCOVERY")
    logger.info(f"Started: {datetime.now().isoformat()}")
    logger.info("=" * 60)

    # Get existing grants from MongoDB
    existing_urls = get_existing_urls()

    if not existing_urls:
        logger.warning("No existing grants found - this may be the first run")

    # Initialize scraper
    scraper = EurekaNetworkScraper()

    # Scrape opportunities
    logger.info("\nFetching opportunities from Eureka Network...")

    if scrape_all_statuses:
        # Full scrape (open, closed, upcoming)
        all_grants = scraper.scrape_all()
    else:
        # Only scrape open and upcoming (most relevant for discovery)
        logger.info("Fetching OPEN opportunities...")
        open_urls = scraper._get_grant_urls("open")

        logger.info("Fetching UPCOMING opportunities...")
        upcoming_urls = scraper._get_grant_urls("upcoming")

        all_urls = list(set(open_urls + upcoming_urls))
        logger.info(f"Found {len(all_urls)} total opportunities ({len(open_urls)} open, {len(upcoming_urls)} upcoming)")

        # Scrape each grant
        all_grants = []
        for i, url in enumerate(all_urls, 1):
            logger.info(f"Scraping {i}/{len(all_urls)}: {url}")
            try:
                grant = scraper._scrape_grant_detail(url)
                if grant:
                    all_grants.append(grant)
            except Exception as e:
                logger.error(f"Failed to scrape {url}: {e}")
                continue

    logger.info(f"\nTotal opportunities scraped: {len(all_grants)}")

    # Identify new opportunities
    new_grants = []
    for grant in all_grants:
        if grant["url"] not in existing_urls:
            new_grants.append(grant)
            logger.info(f"  NEW: {grant['title']}")

    logger.info(f"\nNew opportunities found: {len(new_grants)}")

    return new_grants


def ingest_new_opportunities(grants: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Ingest new opportunities to MongoDB and Pinecone.

    Args:
        grants: List of grant dictionaries to ingest

    Returns:
        Dictionary with success/fail counts
    """
    logger.info("\n" + "=" * 60)
    logger.info("INGESTING NEW OPPORTUNITIES")
    logger.info("=" * 60)

    success_count = 0
    fail_count = 0

    for grant in grants:
        try:
            # Normalize to MongoDB document
            grant_doc = normalize_eureka_grant(grant)

            # Upsert to MongoDB
            if not upsert_to_mongodb(grant_doc):
                logger.warning(f"MongoDB upsert failed for {grant_doc['grant_id']}")
                fail_count += 1
                continue

            # Create embedding
            embedding_text = extract_embedding_text(grant_doc)
            embedding = create_embedding(embedding_text)

            if not embedding:
                logger.warning(f"Embedding creation failed for {grant_doc['grant_id']}")
                fail_count += 1
                continue

            # Upsert to Pinecone
            if not upsert_to_pinecone(grant_doc, embedding):
                logger.warning(f"Pinecone upsert failed for {grant_doc['grant_id']}")
                fail_count += 1
                continue

            success_count += 1
            logger.info(f"  Ingested: {grant_doc['title']}")

        except Exception as e:
            logger.error(f"Failed to ingest {grant.get('title', 'unknown')}: {e}")
            fail_count += 1

    return {"success": success_count, "failed": fail_count}


def write_discovery_summary(
    new_grants: List[Dict[str, Any]],
    ingestion_results: Dict[str, int] = None,
    elapsed_time: float = 0
):
    """Write a summary of the discovery run."""
    summary = {
        "timestamp": datetime.now().isoformat(),
        "new_opportunities_found": len(new_grants),
        "new_opportunities": [
            {
                "title": g["title"],
                "url": g["url"],
                "status": g.get("status", "unknown"),
                "programme": g.get("programme", "unknown"),
                "close_date": g.get("close_date")
            }
            for g in new_grants
        ],
        "elapsed_seconds": elapsed_time,
        "log_file": str(log_file)
    }

    if ingestion_results:
        summary["ingestion"] = ingestion_results

    # Save summary
    summary_file = LOG_DIR / f"discovery_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    summary_file.write_text(json.dumps(summary, indent=2))
    logger.info(f"\nSummary saved to: {summary_file}")

    # Also update latest discovery summary
    latest_file = LOG_DIR / "latest_discovery.json"
    latest_file.write_text(json.dumps(summary, indent=2))

    return summary


def main():
    """Main entry point for discovery script."""
    parser = argparse.ArgumentParser(
        description="Discover new Eureka Network funding opportunities"
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="Automatically ingest new opportunities to MongoDB + Pinecone"
    )
    parser.add_argument(
        "--all-statuses",
        action="store_true",
        help="Scrape all statuses (open, closed, upcoming) instead of just open/upcoming"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report new opportunities, don't save anything"
    )
    args = parser.parse_args()

    start_time = datetime.now()

    try:
        # Validate environment
        if not MONGO_URI:
            logger.error("MONGO_URI environment variable not set!")
            sys.exit(1)

        # Discover new opportunities
        new_grants = discover_new_opportunities(scrape_all_statuses=args.all_statuses)

        # Calculate elapsed time
        elapsed = (datetime.now() - start_time).total_seconds()

        # Report findings
        print("\n" + "=" * 60)
        print("DISCOVERY RESULTS")
        print("=" * 60)

        if new_grants:
            print(f"\nFound {len(new_grants)} NEW opportunities:\n")
            for i, grant in enumerate(new_grants, 1):
                print(f"{i}. {grant['title']}")
                print(f"   URL: {grant['url']}")
                print(f"   Status: {grant.get('status', 'Unknown')}")
                print(f"   Programme: {grant.get('programme', 'Unknown')}")
                if grant.get('close_date'):
                    print(f"   Deadline: {grant['close_date']}")
                print()

            # Ingest if requested
            if args.ingest and not args.dry_run:
                ingestion_results = ingest_new_opportunities(new_grants)
                print(f"\nIngestion complete: {ingestion_results['success']} succeeded, {ingestion_results['failed']} failed")

                if not args.dry_run:
                    write_discovery_summary(new_grants, ingestion_results, elapsed)
            else:
                if not args.dry_run:
                    write_discovery_summary(new_grants, elapsed_time=elapsed)

                if not args.ingest:
                    print("\nTo ingest these opportunities, run again with --ingest flag")
        else:
            print("\nNo new opportunities found.")
            if not args.dry_run:
                write_discovery_summary(new_grants, elapsed_time=elapsed)

        print(f"\nCompleted in {elapsed:.1f} seconds")
        print(f"Log file: {log_file}")

        # Exit with appropriate code
        sys.exit(0)

    except Exception as e:
        logger.error("=" * 60)
        logger.error("DISCOVERY FAILED")
        logger.error(f"Error: {e}")
        logger.error(traceback.format_exc())
        logger.error("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
