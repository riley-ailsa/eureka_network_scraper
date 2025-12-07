#!/usr/bin/env python3
"""
Eureka Network Scraper - Pipeline v3 (Sectioned Schema)

Covers Globalstars, Eurostars, Network Projects, Clusters, and Innowwide.

Usage:
    python run_pipeline.py                    # Full pipeline
    python run_pipeline.py --limit 5          # Test with 5 grants
    python run_pipeline.py --dry-run          # Scrape but don't save
"""

import os
import re
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional


import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from tqdm import tqdm

from ailsa_shared import (
    Grant, GrantSource, GrantStatus, GrantSections,
    SummarySection, EligibilitySection, ScopeSection,
    DatesSection, FundingSection, HowToApplySection,
    AssessmentSection, SupportingInfoSection, ContactsSection,
    ProgrammeInfo, ProcessingInfo, CompetitionType,
    MongoDBClient, PineconeClientV3,
    clean_html, parse_date, parse_money, infer_status_from_dates,
)

load_dotenv()

# =============================================================================
# CONFIGURATION
# =============================================================================

EUREKA_BASE_URL = "https://www.eurekanetwork.org"
EUREKA_CALLS_URL = f"{EUREKA_BASE_URL}/programmes-and-calls/"

# Programme pages to exclude (these are overviews, not individual calls)
EXCLUDE_PATTERNS = [
    '/eurostars/',
    '/innowwide/',
    '/eureka-clusters/',
    '/network-projects/',
    '/globalstars/',
    '/programmes-and-calls/$',
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


# =============================================================================
# DISCOVERY
# =============================================================================

def discover_grant_urls() -> List[str]:
    """Discover all Eureka call URLs."""
    logger.info("Discovering Eureka calls...")
    
    urls = set()
    
    # The main calls page
    try:
        response = requests.get(EUREKA_CALLS_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Error fetching calls page: {e}")
        return []
    
    soup = BeautifulSoup(response.text, 'lxml')
    
    # Find all call links
    for link in soup.select('a[href*="/call/"], a[href*="/calls/"]'):
        href = link.get('href', '')
        
        # Skip excluded patterns
        if any(pattern in href for pattern in EXCLUDE_PATTERNS):
            continue
        
        full_url = href if href.startswith('http') else f"{EUREKA_BASE_URL}{href}"
        urls.add(full_url)
    
    # Also check each programme page for calls
    programme_pages = [
        f"{EUREKA_BASE_URL}/globalstars/",
        f"{EUREKA_BASE_URL}/eurostars/",
        f"{EUREKA_BASE_URL}/network-projects/",
    ]
    
    for page_url in programme_pages:
        try:
            response = requests.get(page_url, headers=HEADERS, timeout=30)
            soup = BeautifulSoup(response.text, 'lxml')
            
            for link in soup.select('a[href*="/call"]'):
                href = link.get('href', '')
                if any(pattern in href for pattern in EXCLUDE_PATTERNS):
                    continue
                full_url = href if href.startswith('http') else f"{EUREKA_BASE_URL}{href}"
                urls.add(full_url)
        except:
            pass
    
    logger.info(f"Discovered {len(urls)} call URLs")
    return list(urls)


# =============================================================================
# SCRAPING
# =============================================================================

def scrape_grant_page(url: str) -> Optional[Dict[str, Any]]:
    """Scrape a single Eureka call page."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None
    
    soup = BeautifulSoup(response.text, 'lxml')
    
    raw = {
        'url': url,
        'scraped_at': datetime.now(timezone.utc),
    }
    
    # Title (usually in header)
    title_elem = soup.select_one('h1, .call-title, [class*="title"]')
    raw['title'] = title_elem.get_text(strip=True) if title_elem else ''
    
    # Programme type from URL or header
    raw['programme'] = detect_programme_from_url(url)
    
    # Dates from header
    for elem in soup.select('[class*="date"], .start-date, .end-date'):
        text = elem.get_text(strip=True).lower()
        if 'start' in text or 'open' in text:
            raw['start_date'] = extract_date_from_text(text)
        elif 'end' in text or 'close' in text or 'deadline' in text:
            raw['end_date'] = extract_date_from_text(text)
    
    # Also look for date patterns in header area
    header = soup.select_one('header, .call-header, [class*="hero"]')
    if header:
        header_text = header.get_text()
        dates = re.findall(r'\d{1,2}\s+\w+\s+\d{4}', header_text)
        if len(dates) >= 2:
            raw['start_date'] = raw.get('start_date') or dates[0]
            raw['end_date'] = raw.get('end_date') or dates[1]
        elif len(dates) == 1:
            raw['end_date'] = raw.get('end_date') or dates[0]
    
    # About this call section
    about = soup.select_one('#about, [id*="about"], .about-section')
    if about:
        raw['about_text'] = about.get_text(separator='\n', strip=True)
    
    # Country information (unique to Eureka)
    countries = []
    country_section = soup.select_one('.countries, [class*="country"], #countries')
    if country_section:
        for country in country_section.select('li, .country-item, span'):
            country_name = country.get_text(strip=True)
            if country_name and len(country_name) < 50:  # Filter out long text
                countries.append(country_name)
    raw['eligible_countries'] = countries
    
    # Application process
    app_section = soup.select_one('#application, [id*="apply"], .application-process')
    if app_section:
        raw['application_process'] = app_section.get_text(separator='\n', strip=True)
    
    # More information / documents
    more_info = soup.select_one('#more-info, [id*="information"], .more-info')
    if more_info:
        raw['more_info'] = more_info.get_text(separator='\n', strip=True)
    
    # Documents
    documents = []
    for link in soup.select('a[href*=".pdf"]'):
        documents.append({
            'title': link.get_text(strip=True) or 'Document',
            'url': link.get('href'),
            'type': 'PDF',
        })
    raw['documents'] = documents
    
    return raw


def detect_programme_from_url(url: str) -> str:
    """Detect Eureka programme from URL."""
    url_lower = url.lower()
    
    if 'globalstars' in url_lower:
        return 'Globalstars'
    elif 'eurostars' in url_lower:
        return 'Eurostars'
    elif 'innowwide' in url_lower:
        return 'Innowwide'
    elif 'cluster' in url_lower:
        return 'Eureka Clusters'
    elif 'network' in url_lower:
        return 'Network Projects'
    return 'Eureka'


def extract_date_from_text(text: str) -> Optional[str]:
    """Extract date from text."""
    match = re.search(r'\d{1,2}\s+\w+\s+\d{4}', text)
    return match.group() if match else None


# =============================================================================
# NORMALIZATION
# =============================================================================

def normalize_grant(raw: Dict[str, Any]) -> Grant:
    """Convert raw Eureka data to Grant schema v3."""
    
    opens_at = parse_date(raw.get('start_date'))
    closes_at = parse_date(raw.get('end_date'))
    status = infer_status_from_dates(opens_at, closes_at)
    
    # Generate ID from URL slug
    url_slug = raw['url'].rstrip('/').split('/')[-1]
    grant_id = f"eureka_{url_slug}"
    
    programme = raw.get('programme', 'Eureka')
    
    # Detect partner country for Globalstars
    partner_country = None
    if programme == 'Globalstars':
        title = raw.get('title', '')
        # Look for "with Japan", "with Korea", etc.
        match = re.search(r'with\s+(\w+)', title)
        if match:
            partner_country = match.group(1)
    
    sections = GrantSections(
        summary=SummarySection(
            text=clean_html(raw.get('about_text', '')),
            call_type=programme,
            extracted_at=datetime.now(timezone.utc),
        ),
        
        eligibility=EligibilitySection(
            text="",
            eligible_countries=raw.get('eligible_countries', []),
            geographic_scope="International",
            partnership_required=True,
            international_partners_allowed=True,
            extracted_at=datetime.now(timezone.utc),
        ),
        
        scope=ScopeSection(
            text=clean_html(raw.get('about_text', '')),
            themes=extract_eureka_themes(raw),
            extracted_at=datetime.now(timezone.utc),
        ),
        
        dates=DatesSection(
            opens_at=opens_at,
            closes_at=closes_at,
            timezone="CET/CEST",
            extracted_at=datetime.now(timezone.utc),
        ),
        
        funding=FundingSection(
            text=extract_funding_text(raw),
            competition_type=CompetitionType.GRANT,
            extracted_at=datetime.now(timezone.utc),
        ),
        
        how_to_apply=HowToApplySection(
            text=clean_html(raw.get('application_process', '')),
            portal_name="Eureka Network",
            portal_url=raw.get('url'),
            extracted_at=datetime.now(timezone.utc),
        ),
        
        assessment=AssessmentSection(
            extracted_at=datetime.now(timezone.utc),
        ),
        
        supporting_info=SupportingInfoSection(
            text=clean_html(raw.get('more_info', '')),
            documents=[
                {'title': d['title'], 'url': d['url'], 'type': d.get('type')}
                for d in raw.get('documents', [])
            ],
            extracted_at=datetime.now(timezone.utc),
        ),
        
        contacts=ContactsSection(
            helpdesk_url="https://www.eurekanetwork.org/contact/",
            extracted_at=datetime.now(timezone.utc),
        ),
    )
    
    programme_info = ProgrammeInfo(
        name=programme,
        funder="Eureka Network",
        eureka_programme=programme,
        partner_country=partner_country,
    )
    
    return Grant(
        grant_id=grant_id,
        source=GrantSource.EUREKA,
        external_id=url_slug,
        title=raw.get('title', ''),
        url=raw.get('url', ''),
        status=status,
        is_active=(status == GrantStatus.OPEN),
        sections=sections,
        programme=programme_info,
        tags=generate_eureka_tags(raw, programme),
        raw=raw,
        processing=ProcessingInfo(
            scraped_at=raw.get('scraped_at'),
            normalized_at=datetime.now(timezone.utc),
            schema_version="3.0",
        ),
    )


# =============================================================================
# HELPERS
# =============================================================================

def extract_eureka_themes(raw: Dict) -> List[str]:
    """Extract themes from Eureka grant."""
    themes = []
    text = (raw.get('about_text', '') + raw.get('title', '')).lower()
    
    theme_map = {
        'ai': 'AI',
        'artificial intelligence': 'AI',
        'digital': 'Digital',
        'health': 'Health',
        'energy': 'Energy',
        'manufacturing': 'Manufacturing',
        'mobility': 'Mobility',
        'space': 'Space',
        'quantum': 'Quantum',
    }
    
    for keyword, theme in theme_map.items():
        if keyword in text and theme not in themes:
            themes.append(theme)
    
    return themes


def extract_funding_text(raw: Dict) -> str:
    """Extract funding info from text."""
    text = raw.get('about_text', '') + raw.get('more_info', '')
    match = re.search(r'(€[\d,.]+ ?(?:million|m)?.*?(?:\.|$))', text, re.IGNORECASE)
    return match.group(0) if match else ''


def generate_eureka_tags(raw: Dict, programme: str) -> List[str]:
    """Generate tags."""
    tags = ['eureka', 'international', 'collaborative']
    
    programme_tag = programme.lower().replace(' ', '_')
    tags.append(programme_tag)
    
    # Add partner country if Globalstars
    countries = raw.get('eligible_countries', [])
    if len(countries) <= 3:
        tags.extend([c.lower() for c in countries])
    
    return tags


# =============================================================================
# INGESTION
# =============================================================================

def ingest_grants(grants: List[Grant], dry_run: bool = False):
    """Save to MongoDB and Pinecone."""
    if dry_run:
        logger.info(f"DRY RUN: Would ingest {len(grants)} grants")
        return
    
    logger.info("Saving to MongoDB...")
    mongo = MongoDBClient()
    success, errors = mongo.upsert_grants(grants)
    logger.info(f"MongoDB: {success} saved, {errors} errors")
    
    logger.info("Creating embeddings...")
    pinecone = PineconeClientV3()
    for grant in tqdm(grants, desc="Embedding"):
        try:
            pinecone.embed_and_upsert_grant(grant)
        except Exception as e:
            logger.error(f"Error embedding {grant.grant_id}: {e}")


# =============================================================================
# MAIN
# =============================================================================

def run_pipeline(limit: Optional[int] = None, dry_run: bool = False):
    """Run the Eureka pipeline."""
    
    logger.info("=" * 60)
    logger.info("Eureka Network Scraper Pipeline v3")
    logger.info("=" * 60)
    
    urls = discover_grant_urls()
    if limit:
        urls = urls[:limit]
    
    logger.info(f"Scraping {len(urls)} calls...")
    raw_grants = []
    for url in tqdm(urls, desc="Scraping"):
        raw = scrape_grant_page(url)
        if raw:
            raw_grants.append(raw)
    
    logger.info(f"Normalizing {len(raw_grants)} grants...")
    grants = []
    for raw in raw_grants:
        try:
            grant = normalize_grant(raw)
            grants.append(grant)
        except Exception as e:
            logger.error(f"Error normalizing: {e}")
    
    ingest_grants(grants, dry_run=dry_run)
    
    logger.info(f"Complete: {len(grants)} grants processed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    
    run_pipeline(limit=args.limit, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
