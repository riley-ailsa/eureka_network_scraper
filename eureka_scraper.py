#!/usr/bin/env python3
"""
Simple scraper for Eureka Network funding opportunities.

Scrapes:
- Open calls: ?status=open
- Closed calls: ?status=closed

Outputs normalized JSON matching the format expected by ingest_to_production.py
"""

import json
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
BASE_URL = "https://www.eurekanetwork.org"
LISTING_URL = BASE_URL + "/programmes-and-calls/"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


class EurekaNetworkScraper:
    """Scraper for Eureka Network grants."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def scrape_all(self) -> List[Dict[str, Any]]:
        """
        Scrape all grants from Eureka Network (open, closed, and upcoming).

        Returns:
            List of normalized grant dictionaries
        """
        logger.info("Starting Eureka Network scrape")

        # Get grant URLs from all statuses
        open_urls = self._get_grant_urls("open")
        closed_urls = self._get_grant_urls("closed")
        upcoming_urls = self._get_grant_urls("upcoming")

        all_urls = open_urls + closed_urls + upcoming_urls
        logger.info(f"Found {len(all_urls)} total grants ({len(open_urls)} open, {len(closed_urls)} closed, {len(upcoming_urls)} upcoming)")

        # Scrape each grant
        grants = []
        for i, url in enumerate(all_urls, 1):
            logger.info(f"Scraping grant {i}/{len(all_urls)}: {url}")
            try:
                grant = self._scrape_grant_detail(url)
                if grant:
                    grants.append(grant)
            except Exception as e:
                logger.error(f"Failed to scrape {url}: {e}")
                continue

        logger.info(f"Successfully scraped {len(grants)}/{len(all_urls)} grants")
        return grants

    def _get_grant_urls(self, status: str) -> List[str]:
        """
        Get grant URLs from a listing page (handles pagination).

        Args:
            status: "open", "closed", or "upcoming"

        Returns:
            List of grant detail URLs
        """
        all_urls = []
        page = 1

        # Exclude generic programme pages (but include investment-readiness calls)
        exclude_patterns = [
            '/programmes-and-calls/eurostars/',
            '/programmes-and-calls/innowwide/',
            '/programmes-and-calls/eureka-clusters/',
            '/programmes-and-calls/network-projects/',
            '/programmes-and-calls/globalstars/',
            '/programmes-and-calls/fast-track-to-the-eic-accelerator/',
            '/programmes-and-calls/investment-readiness/',  # Base path only
            '/programmes-and-calls/',
        ]

        # Exclude pagination pages
        exclude_substrings = [
            '/page/',  # Pagination pages
        ]

        while True:
            url = f"{LISTING_URL}?status={status}&paged={page}"
            logger.info(f"Fetching page {page} for status={status}")

            try:
                resp = self.session.get(url, timeout=15)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, 'html.parser')

                # Find grant links on this page
                page_urls = []

                for link in soup.find_all('a', href=True):
                    href = link['href']
                    # Only include URLs that are call detail pages, not programme overviews
                    if '/programmes-and-calls/' in href:
                        full_url = urljoin(BASE_URL, href)
                        # Remove query params and fragments
                        full_url = full_url.split('?')[0].split('#')[0].rstrip('/')

                        # Check if it's not an excluded pattern
                        is_excluded = False
                        for pattern in exclude_patterns:
                            if full_url == BASE_URL + pattern.rstrip('/'):
                                is_excluded = True
                                break

                        # Check if it contains excluded substrings
                        if not is_excluded:
                            for substring in exclude_substrings:
                                if substring in full_url:
                                    is_excluded = True
                                    break

                        # Only add if not excluded
                        if not is_excluded and full_url not in all_urls:
                            # Make sure it has more path segments (actual calls have longer paths)
                            path_segments = full_url.replace(BASE_URL, '').split('/')
                            # Filter to calls with at least 2 segments: 'programmes-and-calls', 'call-name' (or programme-type/call-name)
                            # Some calls are directly under programmes-and-calls (e.g., network-projects-brazil-sweden-2025)
                            if len([s for s in path_segments if s]) >= 2:
                                page_urls.append(full_url)
                                all_urls.append(full_url)

                logger.info(f"  Found {len(page_urls)} grants on page {page}")

                # Check if there's a next page
                # Look for pagination links or page numbers
                has_next = False
                max_page = page  # Track highest page number found

                for link in soup.find_all('a', href=True):
                    link_text = link.get_text(strip=True)
                    href = link.get('href', '')

                    # Check for "Next" button
                    if 'Next' in link_text or '→' in link_text:
                        has_next = True
                        break

                    # Check for page number links
                    if 'paged=' in href:
                        try:
                            page_num = int(re.search(r'paged=(\d+)', href).group(1))
                            max_page = max(max_page, page_num)
                        except:
                            pass

                # Continue if there's a next link OR we haven't reached the max page number we've seen
                if has_next or page < max_page:
                    page += 1
                else:
                    break

            except Exception as e:
                logger.error(f"Failed to fetch page {page} for status={status}: {e}")
                break

        logger.info(f"Found {len(all_urls)} total grants with status={status}")
        return all_urls

    def _scrape_grant_detail(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Scrape a single grant detail page.

        Args:
            url: Grant detail URL

        Returns:
            Normalized grant dictionary
        """
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')

            # Extract title
            title = self._extract_title(soup)

            # Extract all structured sections from the page
            sections = self._extract_all_sections(soup)

            # Extract description/content (backward compatible)
            description = sections.get('description', '')

            # Extract dates
            open_date, close_date = self._extract_dates(soup)

            # Extract funding info (summary)
            funding_info = self._extract_funding_info(soup)

            # Determine status
            status = self._determine_status(close_date)

            # Generate ID from URL
            grant_id = self._generate_id(url)

            # Extract programme/call type
            programme = self._extract_programme(soup, url)

            # Check if this is supplemental (Investment Readiness)
            is_supplemental = '/investment-readiness/' in url

            # Build normalized grant with full sections
            grant = {
                "id": grant_id,
                "source": "eureka_network",
                "title": title,
                "url": url,
                "status": status,
                "programme": programme,
                "call_id": grant_id.split(':')[1] if ':' in grant_id else None,
                "open_date": open_date.isoformat() if open_date else None,
                "close_date": close_date.isoformat() if close_date else None,
                "is_supplemental": is_supplemental,
                "raw": {
                    "url": url,
                    "title": title,
                    "description": description,
                    "funding_info": funding_info,
                    "scraped_at": datetime.now().isoformat(),
                    "sections": sections,  # All structured content
                    "metadata": {
                        "description": [description] if description else [],
                        "funding_info": [funding_info] if funding_info else [],
                        "is_supplemental": is_supplemental,
                    }
                }
            }

            return grant

        except Exception as e:
            logger.error(f"Error scraping {url}: {e}")
            return None

    def _extract_all_sections(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """
        Extract all structured sections from the grant detail page.

        Returns a dictionary with labeled sections:
        - description: Main description/overview
        - about: About the call
        - eligibility: Eligibility criteria
        - funding: Detailed funding information by country
        - how_to_apply: Application process
        - key_dates: Timeline information
        - country_info: Country-specific requirements
        """
        sections = {}

        # Find main content area
        main_content = soup.find('div', class_='entry-content') or soup.find('main') or soup

        # Extract all headings and their content
        all_headings = main_content.find_all(['h1', 'h2', 'h3', 'h4'])

        for i, heading in enumerate(all_headings):
            heading_text = heading.get_text(strip=True).lower()

            # Get content until next heading
            content_parts = []
            sibling = heading.find_next_sibling()

            while sibling and sibling.name not in ['h1', 'h2', 'h3', 'h4']:
                if sibling.name in ['p', 'ul', 'ol', 'div']:
                    text = sibling.get_text(strip=True)
                    if text:
                        content_parts.append(text)
                sibling = sibling.find_next_sibling()

            content = '\n'.join(content_parts)

            # Categorize based on heading text
            if any(word in heading_text for word in ['about', 'overview', 'description', 'summary']):
                sections['about'] = content
            elif any(word in heading_text for word in ['eligibility', 'eligible', 'who can apply']):
                sections['eligibility'] = content
            elif any(word in heading_text for word in ['funding', 'budget', 'financial support']):
                if 'funding' not in sections:
                    sections['funding'] = {}
                # Check if this is country-specific funding
                if any(country in heading_text for country in ['canada', 'chile', 'sweden', 'brazil', 'france', 'singapore', 'germany', 'israel']):
                    country_name = heading_text.split()[0].title()
                    sections['funding'][country_name] = content
                else:
                    sections['funding']['general'] = content
            elif any(word in heading_text for word in ['how to apply', 'application', 'apply']):
                sections['how_to_apply'] = content
            elif any(word in heading_text for word in ['timeline', 'key dates', 'important dates', 'dates']):
                sections['key_dates'] = content
            elif any(word in heading_text for word in ['country', 'national', 'participating']):
                if 'country_info' not in sections:
                    sections['country_info'] = {}
                sections['country_info'][heading_text] = content

        # Extract description from first few paragraphs if not found
        if 'description' not in sections and 'about' not in sections:
            paragraphs = main_content.find_all('p', limit=5)
            description_parts = []
            for p in paragraphs:
                text = p.get_text(strip=True)
                if text and len(text) > 20:
                    description_parts.append(text)
            sections['description'] = ' '.join(description_parts)
        elif 'about' in sections:
            sections['description'] = sections['about']

        # Extract country-specific accordion content if present
        accordions = main_content.find_all(['div', 'section'], class_=re.compile('accordion|country|toggle', re.I))
        if accordions and 'country_info' not in sections:
            sections['country_info'] = {}
            for accordion in accordions:
                title_elem = accordion.find(['h3', 'h4', 'h5', 'button'])
                if title_elem:
                    country_name = title_elem.get_text(strip=True)
                    content = accordion.get_text(strip=True)
                    if country_name and content:
                        sections['country_info'][country_name] = content

        return sections

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract grant title from page."""
        # Try h1 first
        h1 = soup.find('h1')
        if h1:
            return h1.get_text(strip=True)

        # Fallback to page title
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text(strip=True)
            # Remove site suffix if present
            title = re.sub(r'\s*[-–|]\s*Eureka Network.*$', '', title, flags=re.IGNORECASE)
            return title

        return None

    def _extract_description(self, soup: BeautifulSoup) -> str:
        """Extract main description/content from page."""
        # Try to find main content area
        content_parts = []

        # Look for common content selectors
        selectors = [
            'div.entry-content',
            'div.content',
            'main',
            'article',
        ]

        for selector in selectors:
            content_div = soup.select_one(selector)
            if content_div:
                # Get all text paragraphs
                for p in content_div.find_all(['p', 'li']):
                    text = p.get_text(strip=True)
                    if text and len(text) > 20:  # Skip very short paragraphs
                        content_parts.append(text)
                break

        # If no content found, get all paragraphs
        if not content_parts:
            for p in soup.find_all('p'):
                text = p.get_text(strip=True)
                if text and len(text) > 20:
                    content_parts.append(text)

        return ' '.join(content_parts[:10])  # Limit to first 10 paragraphs

    def _extract_dates(self, soup: BeautifulSoup) -> tuple[Optional[datetime], Optional[datetime]]:
        """Extract opening and closing dates."""
        open_date = None
        close_date = None

        # Look for date patterns in text
        text = soup.get_text()

        # Comprehensive date patterns - try multiple formats
        date_patterns = [
            # Closing/Deadline patterns - various formats
            (r'Submission [Dd]eadline[:\s]+(\d{1,2} [A-Za-z]+ \d{4})', 'close'),
            (r'[Dd]eadline[:\s]+(\d{1,2} [A-Za-z]+ \d{4})', 'close'),
            (r'Closing date[:\s]+(\d{1,2} [A-Za-z]+ \d{4})', 'close'),
            (r'[Cc]loses?[:\s]+(\d{1,2} [A-Za-z]+ \d{4})', 'close'),
            (r'Final [Ss]ubmission[:\s]+(\d{1,2} [A-Za-z]+ \d{4})', 'close'),
            (r'[Ee]nd date[:\s]+(\d{1,2} [A-Za-z]+ \d{4})', 'close'),
            # US format
            (r'Submission [Dd]eadline[:\s]+([A-Za-z]+ \d{1,2},? \d{4})', 'close'),
            (r'[Dd]eadline[:\s]+([A-Za-z]+ \d{1,2},? \d{4})', 'close'),
            (r'[Cc]loses?[:\s]+([A-Za-z]+ \d{1,2},? \d{4})', 'close'),
            # ISO-like format
            (r'[Dd]eadline[:\s]+(\d{4}-\d{2}-\d{2})', 'close'),
            (r'[Cc]loses?[:\s]+(\d{4}-\d{2}-\d{2})', 'close'),
            # Opening patterns
            (r'Apply from[:\s]+(\d{1,2} [A-Za-z]+ \d{4})', 'open'),
            (r'Opens?[:\s]+(\d{1,2} [A-Za-z]+ \d{4})', 'open'),
            (r'Opening date[:\s]+(\d{1,2} [A-Za-z]+ \d{4})', 'open'),
            (r'[Ss]tart date[:\s]+(\d{1,2} [A-Za-z]+ \d{4})', 'open'),
            (r'Applications? open[:\s]+(\d{1,2} [A-Za-z]+ \d{4})', 'open'),
            # US format
            (r'Apply from[:\s]+([A-Za-z]+ \d{1,2},? \d{4})', 'open'),
            (r'Opens?[:\s]+([A-Za-z]+ \d{1,2},? \d{4})', 'open'),
            # ISO-like format
            (r'Opens?[:\s]+(\d{4}-\d{2}-\d{2})', 'open'),
            (r'[Ss]tart[:\s]+(\d{4}-\d{2}-\d{2})', 'open'),
        ]

        for pattern, date_type in date_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    # Try parsing with dateutil (handles most formats)
                    parsed_date = date_parser.parse(match, fuzzy=False)
                    if date_type == 'close' and not close_date:
                        close_date = parsed_date
                    elif date_type == 'open' and not open_date:
                        open_date = parsed_date
                except:
                    # If that fails, try with fuzzy parsing
                    try:
                        parsed_date = date_parser.parse(match, fuzzy=True)
                        if date_type == 'close' and not close_date:
                            close_date = parsed_date
                        elif date_type == 'open' and not open_date:
                            open_date = parsed_date
                    except:
                        continue

        return open_date, close_date

    def _extract_funding_info(self, soup: BeautifulSoup) -> str:
        """Extract funding amount/information."""
        text = soup.get_text()

        # Look for funding amounts - more patterns
        funding_patterns = [
            r'grants of ([\d,]+\s*euro)',
            r'up to ([€$£]\s*[\d,]+(?:\s*(?:million|k|thousand))?)',
            r'maximum of ([€$£]?\s*[\d,]+(?:\s*(?:Canadian dollars|euro|EUR|CAD|dollars))?)',
            r'([€$£]\s*[\d,]+(?:\s*(?:million|k|thousand))?)\s+(?:available|funding)',
            r'([\d,]+\s*(?:euro|EUR|dollars|CAD))',
        ]

        for pattern in funding_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(0)

        return ""

    def _determine_status(self, close_date: Optional[datetime]) -> str:
        """Determine if grant is open or closed based on deadline."""
        if not close_date:
            return "Unknown"

        if close_date > datetime.now(close_date.tzinfo or None):
            return "Open"
        else:
            return "Closed"

    def _generate_id(self, url: str) -> str:
        """Generate unique ID from URL."""
        # Extract slug from URL
        parts = url.rstrip('/').split('/')
        slug = parts[-1] if parts else url

        # Clean slug
        slug = re.sub(r'[^a-z0-9-]', '', slug.lower())

        return f"eureka_network:{slug}"

    def _extract_programme(self, soup: BeautifulSoup, url: str) -> Optional[str]:
        """Extract programme type from URL or breadcrumbs."""
        # Try to extract from URL
        if '/network-projects/' in url:
            return "Network Projects"
        elif '/eurostars/' in url:
            return "Eurostars"
        elif '/globalstars/' in url:
            return "Globalstars"
        elif '/eureka-clusters/' in url:
            return "Eureka Clusters"
        elif '/innowwide/' in url:
            return "Innowwide"

        # Try breadcrumbs
        breadcrumbs = soup.find_all('a', class_=re.compile('breadcrumb'))
        for bc in breadcrumbs:
            text = bc.get_text(strip=True)
            if text and text not in ['Home', 'Programmes and Calls']:
                return text

        return None

    def save_normalized_json(self, grants: List[Dict[str, Any]], output_path: str = "data/eureka_network/normalized.json"):
        """
        Save grants to normalized JSON file.

        Args:
            grants: List of normalized grant dictionaries
            output_path: Output file path
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with output_file.open('w', encoding='utf-8') as f:
            json.dump(grants, f, indent=2, ensure_ascii=False)

        logger.info(f"Saved {len(grants)} grants to {output_path}")


def main():
    """Main entry point."""
    scraper = EurekaNetworkScraper()

    # Scrape all grants
    grants = scraper.scrape_all()

    # Save to normalized JSON
    scraper.save_normalized_json(grants)

    # Print summary
    print(f"\n✅ Scraped {len(grants)} Eureka Network grants")
    print(f"📁 Output: data/eureka_network/normalized.json")
    print(f"\nNext steps:")
    print(f"1. Review the output file")
    print(f"2. Run: python ../path/to/ingest_to_production.py eureka_network")


if __name__ == "__main__":
    main()
