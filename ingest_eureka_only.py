#!/usr/bin/env python3
"""
Ingest Eureka Network grants into MongoDB + Pinecone.
Converts scraped grant data to MongoDB document format with OpenAI embeddings.
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

import openai
from pymongo import MongoClient
from pinecone import Pinecone
from tqdm import tqdm
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "ailsa-grants")
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "ailsa_grants")

if not all([OPENAI_API_KEY, PINECONE_API_KEY, MONGO_URI]):
    print("Missing required environment variables!")
    print("   Required: OPENAI_API_KEY, PINECONE_API_KEY, MONGO_URI")
    sys.exit(1)

openai.api_key = OPENAI_API_KEY

# Initialize clients
pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(PINECONE_INDEX_NAME)
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB_NAME]


def load_eureka_grants() -> List[Dict[str, Any]]:
    """Load Eureka Network grants from normalized.json"""
    file_path = Path("data/eureka_network/normalized.json")

    if not file_path.exists():
        print(f"File not found: {file_path}")
        print(f"   Run scraper first: python eureka_scraper.py")
        return []

    grants = json.loads(file_path.read_text(encoding='utf-8'))
    print(f"Loaded {len(grants)} grants from Eureka Network")
    return grants


def extract_sectors(grant: Dict[str, Any]) -> List[str]:
    """Extract sectors/categories from grant data if available."""
    sectors = []
    raw = grant.get('raw', {})
    sections = raw.get('sections', {})

    # Try to extract from eligibility or about sections
    about = sections.get('about', '') or sections.get('description', '')
    eligibility = sections.get('eligibility', '')

    # Common sector keywords to look for
    sector_keywords = {
        'technology': ['technology', 'tech', 'digital', 'software', 'hardware'],
        'healthcare': ['health', 'medical', 'biotech', 'pharma', 'life sciences'],
        'energy': ['energy', 'renewable', 'clean tech', 'sustainability'],
        'manufacturing': ['manufacturing', 'industrial', 'production'],
        'aerospace': ['aerospace', 'aviation', 'space'],
        'automotive': ['automotive', 'vehicle', 'mobility'],
        'agriculture': ['agriculture', 'agri', 'food', 'agtech'],
        'environment': ['environment', 'climate', 'green'],
    }

    text_to_search = f"{about} {eligibility}".lower()

    for sector, keywords in sector_keywords.items():
        if any(kw in text_to_search for kw in keywords):
            sectors.append(sector)

    return sectors


def normalize_eureka_grant(grant: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize Eureka grant to MongoDB document schema.

    Follows the schema specified in SPEC 2 for consistent grant storage.
    """
    raw = grant.get('raw', {})
    sections = raw.get('sections', {})

    # Get description from various possible sources
    description = raw.get('description', '') or sections.get('about', '') or sections.get('description', '')

    # Parse dates to datetime objects if they're strings
    open_date = grant.get('open_date')
    close_date = grant.get('close_date')

    if isinstance(open_date, str) and open_date:
        try:
            open_date = datetime.fromisoformat(open_date.replace('Z', '+00:00'))
        except ValueError:
            open_date = None

    if isinstance(close_date, str) and close_date:
        try:
            close_date = datetime.fromisoformat(close_date.replace('Z', '+00:00'))
        except ValueError:
            close_date = None

    # Determine status
    status = grant.get('status', 'Unknown').lower()
    if status not in ['open', 'closed', 'upcoming']:
        status = 'unknown'

    # Build programme tag
    programme = grant.get('programme', '')
    programme_tag = programme.lower().replace(' ', '_') if programme else ''

    # Build tags list
    tags = ['eureka']
    if programme_tag:
        tags.append(programme_tag)
    if grant.get('is_supplemental'):
        tags.append('investment_readiness')

    # Extract sectors
    sectors = extract_sectors(grant)

    now = datetime.utcnow()

    return {
        # Primary identifiers
        "grant_id": f"eureka_{grant.get('call_id', grant['id'].split(':')[-1])}",
        "source": "eureka",
        "external_id": grant.get('call_id', grant['id'].split(':')[-1]),

        # Core metadata
        "title": grant['title'],
        "url": grant['url'],
        "description": description[:2000] if description else "",

        # Status & dates
        "status": status,
        "is_active": status == "open",
        "opens_at": open_date,
        "closes_at": close_date,

        # Funding
        "total_fund_gbp": None,  # Eureka often doesn't specify total pot
        "total_fund_display": raw.get('funding_info'),
        "project_funding_min": None,
        "project_funding_max": None,
        "competition_type": "grant",

        # Programme info
        "programme": programme,

        # Classification
        "tags": tags,
        "sectors": sectors,

        # Raw data (Eureka has less structured data, keep more for re-parsing)
        "raw": {
            "description": raw.get('description'),
            "funding_info": raw.get('funding_info'),
            "sections": sections,
            "is_supplemental": grant.get('is_supplemental', False),
            "original_id": grant['id'],
            "original_source": grant['source'],
        },

        # Timestamps
        "scraped_at": now,
        "updated_at": now,
    }


def extract_embedding_text(grant_doc: Dict[str, Any]) -> str:
    """Extract rich text for embedding from normalized grant document."""
    parts = []

    # Title
    if grant_doc.get('title'):
        parts.append(f"Title: {grant_doc['title']}")

    # Programme
    if grant_doc.get('programme'):
        parts.append(f"Programme: {grant_doc['programme']}")

    # Source
    parts.append("Source: Eureka Network")

    # Type
    raw = grant_doc.get('raw', {})
    if raw.get('is_supplemental'):
        parts.append("Type: Investment Readiness (Supplemental)")
    else:
        parts.append("Type: R&D Grant")

    # Status and dates
    if grant_doc.get('status'):
        parts.append(f"Status: {grant_doc['status']}")

    if grant_doc.get('opens_at'):
        parts.append(f"Opens: {grant_doc['opens_at'].isoformat() if isinstance(grant_doc['opens_at'], datetime) else grant_doc['opens_at']}")

    if grant_doc.get('closes_at'):
        parts.append(f"Deadline: {grant_doc['closes_at'].isoformat() if isinstance(grant_doc['closes_at'], datetime) else grant_doc['closes_at']}")

    # Description
    if grant_doc.get('description'):
        desc_text = grant_doc['description'][:1000]
        parts.append(f"\nDescription:\n{desc_text}")

    # Extract from raw sections
    sections = raw.get('sections', {})

    # About section (if different from description)
    if sections.get('about'):
        about_text = sections['about'][:900]
        if about_text not in grant_doc.get('description', ''):
            parts.append(f"\nAbout:\n{about_text}")

    # Eligibility
    if sections.get('eligibility'):
        eligibility_text = sections['eligibility'][:750]
        parts.append(f"\nEligibility:\n{eligibility_text}")

    # Funding information
    if sections.get('funding'):
        funding_section = sections['funding']
        if isinstance(funding_section, dict):
            parts.append("\nFunding:")
            for country, info in list(funding_section.items())[:5]:
                info_text = info[:400] if len(info) > 400 else info
                parts.append(f"  {country}: {info_text}")
        else:
            funding_text = funding_section[:600]
            parts.append(f"\nFunding:\n{funding_text}")

    # Key dates
    if sections.get('key_dates'):
        dates_text = sections['key_dates'][:450]
        parts.append(f"\nKey Dates:\n{dates_text}")

    # How to apply
    if sections.get('how_to_apply'):
        apply_text = sections['how_to_apply'][:550]
        parts.append(f"\nHow to Apply:\n{apply_text}")

    # Country-specific information
    if sections.get('country_info'):
        country_info = sections['country_info']
        if isinstance(country_info, dict):
            parts.append("\nCountry Information:")
            for country, info in list(country_info.items())[:3]:
                info_text = info[:300] if len(info) > 300 else info
                parts.append(f"  {country}: {info_text}")

    # Sectors
    if grant_doc.get('sectors'):
        parts.append(f"\nSectors: {', '.join(grant_doc['sectors'])}")

    return "\n".join(parts)


def create_embedding(text: str) -> Optional[List[float]]:
    """Generate embedding using OpenAI."""
    try:
        response = openai.embeddings.create(
            input=text,
            model="text-embedding-3-small"
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"Error creating embedding: {e}")
        return None


def upsert_to_mongodb(grant_doc: Dict[str, Any]) -> bool:
    """Upsert grant document to MongoDB."""
    try:
        result = db.grants.update_one(
            {"grant_id": grant_doc["grant_id"]},
            {
                "$set": grant_doc,
                "$setOnInsert": {"created_at": datetime.utcnow()}
            },
            upsert=True
        )
        return True
    except Exception as e:
        print(f"Error upserting to MongoDB: {e}")
        return False


def upsert_to_pinecone(grant_doc: Dict[str, Any], embedding: List[float]) -> bool:
    """Upsert grant to Pinecone."""
    try:
        # Prepare metadata (Pinecone has metadata size limits)
        metadata = {
            'source': 'eureka',
            'title': grant_doc['title'][:500],
            'status': grant_doc.get('status', 'unknown'),
            'url': grant_doc['url'],
        }

        if grant_doc.get('programme'):
            metadata['programme'] = grant_doc['programme'][:100]

        if grant_doc.get('opens_at'):
            metadata['opens_at'] = grant_doc['opens_at'].isoformat() if isinstance(grant_doc['opens_at'], datetime) else str(grant_doc['opens_at'])

        if grant_doc.get('closes_at'):
            metadata['closes_at'] = grant_doc['closes_at'].isoformat() if isinstance(grant_doc['closes_at'], datetime) else str(grant_doc['closes_at'])

        if grant_doc.get('is_active') is not None:
            metadata['is_active'] = grant_doc['is_active']

        # Upsert to Pinecone
        index.upsert(
            vectors=[{
                'id': grant_doc['grant_id'],
                'values': embedding,
                'metadata': metadata
            }]
        )
        return True
    except Exception as e:
        print(f"Error upserting to Pinecone: {e}")
        return False


def ingest_eureka_grants(grants: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Process list of grants from eureka_scraper.py.

    Args:
        grants: Raw grants from scraper

    Returns:
        Dictionary with success/fail counts
    """
    success_count = 0
    fail_count = 0

    for grant in tqdm(grants, desc="Processing grants"):
        try:
            # Normalize to MongoDB doc
            grant_doc = normalize_eureka_grant(grant)

            # Upsert to MongoDB
            if not upsert_to_mongodb(grant_doc):
                fail_count += 1
                continue

            # Generate embedding
            embedding_text = extract_embedding_text(grant_doc)
            embedding = create_embedding(embedding_text)

            if not embedding:
                print(f"Skipping Pinecone for {grant_doc['grant_id']} - embedding failed")
                fail_count += 1
                continue

            # Upsert to Pinecone
            if not upsert_to_pinecone(grant_doc, embedding):
                fail_count += 1
                continue

            success_count += 1

        except Exception as e:
            print(f"Failed to ingest {grant.get('title')}: {e}")
            fail_count += 1

    return {'success': success_count, 'failed': fail_count}


def main():
    """Main ingestion pipeline."""
    print("=" * 60)
    print("EUREKA NETWORK GRANT INGESTION (MongoDB)")
    print("=" * 60)

    # Load grants
    grants = load_eureka_grants()
    if not grants:
        print("No grants to ingest")
        return

    # Filter options
    print(f"\nTotal grants loaded: {len(grants)}")
    primary = [g for g in grants if not g.get('is_supplemental', False)]
    supplemental = [g for g in grants if g.get('is_supplemental', False)]
    print(f"   - Primary R&D grants: {len(primary)}")
    print(f"   - Supplemental (Investment Readiness): {len(supplemental)}")

    # Ask user what to ingest
    print("\nWhat would you like to ingest?")
    print(f"1. All grants ({len(grants)} total)")
    print(f"2. Primary R&D grants only ({len(primary)} grants)")
    print(f"3. Supplemental opportunities only ({len(supplemental)} grants)")

    choice = input("\nEnter choice (1-3) [default: 1]: ").strip() or "1"

    if choice == "2":
        grants_to_ingest = primary
        print(f"\nIngesting {len(grants_to_ingest)} primary R&D grants")
    elif choice == "3":
        grants_to_ingest = supplemental
        print(f"\nIngesting {len(grants_to_ingest)} supplemental opportunities")
    else:
        grants_to_ingest = grants
        print(f"\nIngesting all {len(grants_to_ingest)} grants")

    # Process grants
    print(f"\nStarting ingestion...")
    results = ingest_eureka_grants(grants_to_ingest)

    # Print summary
    print("\n" + "=" * 60)
    print("INGESTION COMPLETE")
    print("=" * 60)
    print(f"MongoDB + Pinecone: {results['success']}/{len(grants_to_ingest)} grants ingested")

    if results['success'] == len(grants_to_ingest):
        print("\nAll grants successfully ingested!")
    else:
        print(f"\nSome grants failed to ingest")
        print(f"   Failures: {results['failed']}")

    # Print verification command
    print("\n" + "-" * 60)
    print("Verify with:")
    print(f"  mongosh \"{MONGO_URI}\" --eval 'use {MONGO_DB_NAME}; db.grants.countDocuments({{source: \"eureka\"}})'")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nIngestion cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # Close connections
        if mongo_client:
            mongo_client.close()
