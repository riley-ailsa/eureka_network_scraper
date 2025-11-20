#!/usr/bin/env python3
"""
Ingest Eureka Network grants into production PostgreSQL + Pinecone.
Simplified version that only handles Eureka Network data.
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

import psycopg2
import openai
from pinecone import Pinecone
from tqdm import tqdm
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "ailsa-grants")
DATABASE_URL = os.getenv("DATABASE_URL")

if not all([OPENAI_API_KEY, PINECONE_API_KEY, DATABASE_URL]):
    print("❌ Missing required environment variables!")
    print("   Required: OPENAI_API_KEY, PINECONE_API_KEY, DATABASE_URL")
    sys.exit(1)

openai.api_key = OPENAI_API_KEY

# Initialize clients
pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(PINECONE_INDEX_NAME)
pg_conn = psycopg2.connect(DATABASE_URL)


def load_eureka_grants() -> List[Dict[str, Any]]:
    """Load Eureka Network grants from normalized.json"""
    file_path = Path("data/eureka_network/normalized.json")

    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        print(f"   Run scraper first: python eureka_scraper.py")
        return []

    grants = json.loads(file_path.read_text(encoding='utf-8'))
    print(f"📁 Loaded {len(grants)} grants from Eureka Network")
    return grants


def extract_embedding_text(grant: Dict[str, Any]) -> str:
    """Extract rich text for embedding from Eureka grant using structured sections"""
    parts = []

    # Title
    if grant.get('title'):
        parts.append(f"Title: {grant['title']}")

    # Programme
    if grant.get('programme'):
        parts.append(f"Programme: {grant['programme']}")

    # Source
    parts.append("Source: Eureka Network")

    # Supplemental flag
    if grant.get('is_supplemental'):
        parts.append("Type: Investment Readiness (Supplemental)")
    else:
        parts.append("Type: R&D Grant")

    # Status and dates
    if grant.get('status'):
        parts.append(f"Status: {grant['status']}")

    if grant.get('open_date'):
        parts.append(f"Opens: {grant['open_date']}")

    if grant.get('close_date'):
        parts.append(f"Deadline: {grant['close_date']}")

    # Extract from structured sections
    raw = grant.get('raw', {})
    sections = raw.get('sections', {})

    # About/Description
    if sections.get('about'):
        about_text = sections['about']
        if len(about_text) > 1000:
            about_text = about_text[:900] + "..."
        parts.append(f"\nAbout:\n{about_text}")
    elif sections.get('description'):
        desc_text = sections['description']
        if len(desc_text) > 1000:
            desc_text = desc_text[:900] + "..."
        parts.append(f"\nDescription:\n{desc_text}")

    # Eligibility
    if sections.get('eligibility'):
        eligibility_text = sections['eligibility']
        if len(eligibility_text) > 800:
            eligibility_text = eligibility_text[:750] + "..."
        parts.append(f"\nEligibility:\n{eligibility_text}")

    # Funding information
    if sections.get('funding'):
        funding_section = sections['funding']
        if isinstance(funding_section, dict):
            parts.append("\nFunding:")
            for country, info in funding_section.items():
                info_text = info[:400] + "..." if len(info) > 400 else info
                parts.append(f"  {country}: {info_text}")
        else:
            funding_text = funding_section[:600] + "..." if len(funding_section) > 600 else funding_section
            parts.append(f"\nFunding:\n{funding_text}")

    # Key dates
    if sections.get('key_dates'):
        dates_text = sections['key_dates']
        if len(dates_text) > 500:
            dates_text = dates_text[:450] + "..."
        parts.append(f"\nKey Dates:\n{dates_text}")

    # How to apply
    if sections.get('how_to_apply'):
        apply_text = sections['how_to_apply']
        if len(apply_text) > 600:
            apply_text = apply_text[:550] + "..."
        parts.append(f"\nHow to Apply:\n{apply_text}")

    # Country-specific information
    if sections.get('country_info'):
        country_info = sections['country_info']
        if isinstance(country_info, dict):
            parts.append("\nCountry Information:")
            for country, info in list(country_info.items())[:3]:  # Limit to 3 countries to avoid token limit
                info_text = info[:300] + "..." if len(info) > 300 else info
                parts.append(f"  {country}: {info_text}")

    return "\n".join(parts)


def create_embedding(text: str) -> List[float]:
    """Generate embedding using OpenAI"""
    try:
        response = openai.embeddings.create(
            input=text,
            model="text-embedding-3-small"
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"⚠️  Error creating embedding: {e}")
        return None


def insert_to_postgres(grant: Dict[str, Any], cursor):
    """Insert grant into PostgreSQL grants table"""
    try:
        # Extract summary (first 500 chars of description)
        raw = grant.get('raw', {})
        description = raw.get('description', '')
        summary = description[:500] if description else ''

        cursor.execute("""
            INSERT INTO grants (
                grant_id, source, title, url, call_id, status, programme,
                open_date, close_date, description_summary, scraped_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (grant_id) DO UPDATE SET
                title = EXCLUDED.title,
                url = EXCLUDED.url,
                status = EXCLUDED.status,
                programme = EXCLUDED.programme,
                open_date = EXCLUDED.open_date,
                close_date = EXCLUDED.close_date,
                description_summary = EXCLUDED.description_summary,
                updated_at = NOW()
        """, (
            grant['id'],
            grant['source'],
            grant['title'],
            grant['url'],
            grant.get('call_id'),
            grant.get('status'),
            grant.get('programme'),
            grant.get('open_date'),
            grant.get('close_date'),
            summary
        ))
        return True
    except Exception as e:
        print(f"⚠️  Error inserting to PostgreSQL: {e}")
        return False


def upsert_to_pinecone(grant: Dict[str, Any], embedding: List[float]):
    """Upsert grant to Pinecone"""
    try:
        # Prepare metadata
        metadata = {
            'source': grant['source'],
            'title': grant['title'],
            'url': grant['url'],
            'status': grant.get('status', 'Unknown'),
            'is_supplemental': grant.get('is_supplemental', False),
        }

        if grant.get('programme'):
            metadata['programme'] = grant['programme']

        if grant.get('open_date'):
            metadata['open_date'] = grant['open_date']

        if grant.get('close_date'):
            metadata['close_date'] = grant['close_date']

        # Upsert to Pinecone
        index.upsert(
            vectors=[{
                'id': grant['id'],
                'values': embedding,
                'metadata': metadata
            }]
        )
        return True
    except Exception as e:
        print(f"⚠️  Error upserting to Pinecone: {e}")
        return False


def main():
    """Main ingestion pipeline"""
    print("=" * 60)
    print("EUREKA NETWORK GRANT INGESTION")
    print("=" * 60)

    # Load grants
    grants = load_eureka_grants()
    if not grants:
        print("❌ No grants to ingest")
        return

    # Filter options
    print(f"\n📊 Total grants loaded: {len(grants)}")
    primary = [g for g in grants if not g.get('is_supplemental', False)]
    supplemental = [g for g in grants if g.get('is_supplemental', False)]
    print(f"   - Primary R&D grants: {len(primary)}")
    print(f"   - Supplemental (Investment Readiness): {len(supplemental)}")

    # Ask user what to ingest
    print("\nWhat would you like to ingest?")
    print("1. All grants (40 total)")
    print("2. Primary R&D grants only (29 grants)")
    print("3. Supplemental opportunities only (11 grants)")

    choice = input("\nEnter choice (1-3) [default: 1]: ").strip() or "1"

    if choice == "2":
        grants_to_ingest = primary
        print(f"\n✅ Ingesting {len(grants_to_ingest)} primary R&D grants")
    elif choice == "3":
        grants_to_ingest = supplemental
        print(f"\n✅ Ingesting {len(grants_to_ingest)} supplemental opportunities")
    else:
        grants_to_ingest = grants
        print(f"\n✅ Ingesting all {len(grants_to_ingest)} grants")

    # Create database cursor
    cursor = pg_conn.cursor()

    # Process each grant
    success_pg = 0
    success_pc = 0

    print(f"\n🚀 Starting ingestion...")

    for grant in tqdm(grants_to_ingest, desc="Processing grants"):
        # Extract embedding text
        embedding_text = extract_embedding_text(grant)

        # Create embedding
        embedding = create_embedding(embedding_text)
        if not embedding:
            print(f"\n⚠️  Skipping {grant['id']} - embedding failed")
            continue

        # Insert to PostgreSQL
        if insert_to_postgres(grant, cursor):
            success_pg += 1

        # Upsert to Pinecone
        if upsert_to_pinecone(grant, embedding):
            success_pc += 1

    # Commit PostgreSQL changes
    pg_conn.commit()
    cursor.close()

    # Print summary
    print("\n" + "=" * 60)
    print("INGESTION COMPLETE")
    print("=" * 60)
    print(f"✅ PostgreSQL: {success_pg}/{len(grants_to_ingest)} grants inserted")
    print(f"✅ Pinecone: {success_pc}/{len(grants_to_ingest)} grants indexed")

    if success_pg == len(grants_to_ingest) and success_pc == len(grants_to_ingest):
        print("\n🎉 All grants successfully ingested!")
    else:
        print(f"\n⚠️  Some grants failed to ingest")
        print(f"   PostgreSQL failures: {len(grants_to_ingest) - success_pg}")
        print(f"   Pinecone failures: {len(grants_to_ingest) - success_pc}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Ingestion cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # Close connections
        if pg_conn:
            pg_conn.close()
