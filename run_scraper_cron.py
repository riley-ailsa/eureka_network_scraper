#!/usr/bin/env python3
"""
Cron wrapper for Eureka Network scraper + ingestion pipeline.
Runs scraping and ingestion automatically with proper logging and error handling.
Uses MongoDB for grant storage.
"""

import sys
import os
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List
import traceback

import openai
from pymongo import MongoClient
from pinecone import Pinecone
from dotenv import load_dotenv

# Import scraper and ingestion functions
from eureka_scraper import EurekaNetworkScraper
from ingest_eureka_only import (
    normalize_eureka_grant,
    extract_embedding_text,
    create_embedding,
    upsert_to_pinecone
)

# Load environment
load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "ailsa-grants")
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "ailsa_grants")
LOG_DIR = Path("logs")
DATA_DIR = Path("data/eureka_network")

# Create directories
LOG_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Setup logging
log_file = LOG_DIR / f"cron_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def validate_environment() -> bool:
    """Validate all required environment variables are set."""
    if not all([OPENAI_API_KEY, PINECONE_API_KEY, MONGO_URI]):
        logger.error("Missing required environment variables!")
        logger.error("Required: OPENAI_API_KEY, PINECONE_API_KEY, MONGO_URI")
        return False
    return True


def run_scraper() -> List[Dict[str, Any]]:
    """
    Run the Eureka Network scraper.

    Returns:
        List of scraped grants
    """
    logger.info("=" * 60)
    logger.info("STEP 1: SCRAPING EUREKA NETWORK GRANTS")
    logger.info("=" * 60)

    try:
        scraper = EurekaNetworkScraper()
        grants = scraper.scrape_all()

        logger.info(f"Scraped {len(grants)} grants")

        # Save to file
        output_file = DATA_DIR / "normalized.json"
        output_file.write_text(json.dumps(grants, indent=2, ensure_ascii=False))
        logger.info(f"Saved to {output_file}")

        return grants

    except Exception as e:
        logger.error(f"Scraping failed: {e}")
        logger.error(traceback.format_exc())
        raise


def run_ingestion(grants: List[Dict[str, Any]], ingest_all: bool = True) -> Dict[str, int]:
    """
    Run the ingestion pipeline to MongoDB + Pinecone.

    Args:
        grants: List of grants to ingest
        ingest_all: If True, ingest all grants. If False, only primary R&D grants.

    Returns:
        Dictionary with success counts
    """
    logger.info("=" * 60)
    logger.info("STEP 2: INGESTING TO MONGODB + PINECONE")
    logger.info("=" * 60)

    # Filter grants if needed
    if not ingest_all:
        primary = [g for g in grants if not g.get('is_supplemental', False)]
        logger.info(f"Filtering to {len(primary)} primary R&D grants (excluding {len(grants) - len(primary)} supplemental)")
        grants_to_ingest = primary
    else:
        grants_to_ingest = grants
        logger.info(f"Ingesting all {len(grants_to_ingest)} grants")

    # Initialize clients
    try:
        openai.api_key = OPENAI_API_KEY
        pc = Pinecone(api_key=PINECONE_API_KEY)
        index = pc.Index(PINECONE_INDEX_NAME)
        mongo_client = MongoClient(MONGO_URI)
        db = mongo_client[MONGO_DB_NAME]
        logger.info("Connected to OpenAI, Pinecone, and MongoDB")
    except Exception as e:
        logger.error(f"Failed to connect to services: {e}")
        raise

    # Process each grant
    success_mongo = 0
    success_pc = 0
    failed_grants = []

    for i, grant in enumerate(grants_to_ingest, 1):
        grant_id = grant.get('id', 'unknown')
        logger.info(f"Processing {i}/{len(grants_to_ingest)}: {grant_id}")

        try:
            # Normalize to MongoDB document
            grant_doc = normalize_eureka_grant(grant)

            # Upsert to MongoDB
            try:
                db.grants.update_one(
                    {"grant_id": grant_doc["grant_id"]},
                    {
                        "$set": grant_doc,
                        "$setOnInsert": {"created_at": datetime.utcnow()}
                    },
                    upsert=True
                )
                success_mongo += 1
                logger.info(f"  MongoDB OK")
            except Exception as e:
                logger.warning(f"  MongoDB failed for {grant_id}: {e}")
                failed_grants.append((grant_id, "mongo_failed"))

            # Create embedding
            embedding_text = extract_embedding_text(grant_doc)
            embedding = create_embedding(embedding_text)

            if not embedding:
                logger.warning(f"  Skipping Pinecone - embedding failed")
                failed_grants.append((grant_id, "embedding_failed"))
                continue

            # Upsert to Pinecone
            if upsert_to_pinecone(grant_doc, embedding):
                success_pc += 1
                logger.info(f"  Pinecone OK")
            else:
                logger.warning(f"  Pinecone failed for {grant_id}")
                failed_grants.append((grant_id, "pinecone_failed"))

        except Exception as e:
            logger.error(f"Error processing {grant_id}: {e}")
            logger.error(traceback.format_exc())
            failed_grants.append((grant_id, str(e)))
            continue

    # Close MongoDB connection
    mongo_client.close()

    # Log summary
    logger.info("=" * 60)
    logger.info("INGESTION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"MongoDB: {success_mongo}/{len(grants_to_ingest)} grants inserted")
    logger.info(f"Pinecone: {success_pc}/{len(grants_to_ingest)} grants indexed")

    if failed_grants:
        logger.warning(f"{len(failed_grants)} grants had issues:")
        for grant_id, reason in failed_grants[:10]:  # Show first 10
            logger.warning(f"  - {grant_id}: {reason}")

    return {
        "total": len(grants_to_ingest),
        "mongo_success": success_mongo,
        "pinecone_success": success_pc,
        "failed": len(failed_grants)
    }


def write_run_summary(grants_count: int, ingestion_results: Dict[str, int], elapsed_time: float):
    """Write a summary of this run to a JSON file."""
    summary = {
        "timestamp": datetime.now().isoformat(),
        "scraping": {
            "grants_found": grants_count
        },
        "ingestion": ingestion_results,
        "elapsed_seconds": elapsed_time,
        "log_file": str(log_file)
    }

    summary_file = LOG_DIR / f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    summary_file.write_text(json.dumps(summary, indent=2))
    logger.info(f"Run summary saved to {summary_file}")

    # Also update "latest" summary
    latest_summary = LOG_DIR / "latest_run.json"
    latest_summary.write_text(json.dumps(summary, indent=2))


def main():
    """Main cron job entry point."""
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("EUREKA NETWORK CRON JOB STARTED")
    logger.info(f"Timestamp: {start_time.isoformat()}")
    logger.info("=" * 60)

    try:
        # Validate environment
        if not validate_environment():
            logger.error("Environment validation failed")
            sys.exit(1)

        # Run scraper
        grants = run_scraper()

        if not grants:
            logger.warning("No grants scraped - skipping ingestion")
            sys.exit(0)

        # Run ingestion (all grants by default, change to False for primary only)
        ingestion_results = run_ingestion(grants, ingest_all=True)

        # Calculate elapsed time
        elapsed = (datetime.now() - start_time).total_seconds()

        # Write summary
        write_run_summary(len(grants), ingestion_results, elapsed)

        # Final status
        logger.info("=" * 60)
        logger.info("CRON JOB COMPLETED SUCCESSFULLY")
        logger.info(f"Total time: {elapsed:.1f} seconds")
        logger.info("=" * 60)

        # Exit with appropriate code
        if ingestion_results["failed"] > 0:
            logger.warning("Some grants failed - check logs")
            sys.exit(2)  # Partial success
        else:
            sys.exit(0)  # Full success

    except Exception as e:
        logger.error("=" * 60)
        logger.error("CRON JOB FAILED")
        logger.error(f"Error: {e}")
        logger.error(traceback.format_exc())
        logger.error("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
