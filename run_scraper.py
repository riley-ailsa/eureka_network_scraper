#!/usr/bin/env python3
"""Main entry point for Eureka scraper."""

from src.scraper import EurekaNetworkScraper
from src.ingest import ingest_eureka_grants

if __name__ == "__main__":
    # Run scraper
    scraper = EurekaNetworkScraper()
    grants = scraper.scrape_all()

    # Ingest to MongoDB + Pinecone
    if grants:
        ingest_eureka_grants(grants)
    else:
        print("No grants found to ingest")
