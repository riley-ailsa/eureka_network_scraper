"""
Eureka Network v3 Normalizer

Converts Eureka scraped grants into ailsa_shared Grant schema with
9 independently embeddable sections for RAG.

Section mapping:
    Eureka Section       → v3 Section
    ─────────────────────────────────────────
    about/description    → summary.text
    eligibility          → eligibility.text
    funding              → funding + scope
    key_dates            → dates
    how_to_apply         → how_to_apply.text
    country_info         → eligibility.eligible_countries
"""

import re
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple, List, Dict, Any
from pathlib import Path

from ailsa_shared.models import (
    Grant,
    GrantSource,
    GrantStatus,
    GrantSections,
    SummarySection,
    EligibilitySection,
    ScopeSection,
    DatesSection,
    FundingSection,
    HowToApplySection,
    AssessmentSection,
    SupportingInfoSection,
    ContactsSection,
    Contact,
    ProgrammeInfo,
    ProcessingInfo,
    CompetitionType,
)

logger = logging.getLogger(__name__)


# =============================================================================
# MAIN NORMALIZER
# =============================================================================

def normalize_eureka_v3(grant_data: Dict[str, Any]) -> Grant:
    """
    Normalize Eureka grant data to v3 Grant schema.
    
    Args:
        grant_data: Dict from normalized.json
        
    Returns:
        Grant with all sections populated
    """
    raw = grant_data.get('raw', {})
    sections = raw.get('sections', {})
    
    # Build grant_id
    grant_id = grant_data.get('id', '').replace('eureka_network:', 'eureka_')
    if not grant_id.startswith('eureka_'):
        grant_id = f"eureka_{grant_data.get('call_id', 'unknown')}"
    
    # Get basic fields
    title = grant_data.get('title', '')
    url = grant_data.get('url', '')
    programme = grant_data.get('programme', '')
    
    # Parse dates
    opens_at = _parse_date(grant_data.get('open_date'))
    closes_at = _parse_date(grant_data.get('close_date'))
    
    # Determine status
    status = _infer_status(grant_data.get('status'), opens_at, closes_at)
    is_active = status == GrantStatus.OPEN
    
    # Get section text
    about_text = sections.get('about', '') or sections.get('description', '') or raw.get('description', '')
    eligibility_text = sections.get('eligibility', '')
    funding_section = sections.get('funding', {})
    key_dates_text = sections.get('key_dates', '')
    how_to_apply_text = sections.get('how_to_apply', '')
    country_info = sections.get('country_info', {})
    
    # Parse funding
    funding_text = _get_funding_text(funding_section)
    total_pot_gbp, total_pot_eur, total_pot_display = _parse_funding_amount(raw.get('funding_info', ''), funding_text)
    per_project_min, per_project_max, per_project_display = _parse_per_project_funding(funding_text, about_text)
    
    # Extract countries from country_info
    eligible_countries = _extract_countries(country_info, title)
    
    # Build sections
    grant_sections = GrantSections(
        summary=_build_summary_section(about_text, programme),
        eligibility=_build_eligibility_section(eligibility_text, country_info, eligible_countries),
        scope=_build_scope_section(about_text, funding_text, title),
        dates=_build_dates_section(opens_at, closes_at, key_dates_text),
        funding=_build_funding_section(
            total_pot_gbp, total_pot_eur, total_pot_display,
            per_project_min, per_project_max, per_project_display,
            funding_text
        ),
        how_to_apply=_build_how_to_apply_section(how_to_apply_text),
        assessment=_build_assessment_section(how_to_apply_text, eligibility_text),
        supporting_info=SupportingInfoSection(extracted_at=_now()),
        contacts=_build_contacts_section(country_info),
    )
    
    # Build programme info
    programme_info = _build_programme_info(programme, grant_data.get('call_id', ''))
    
    # Build tags
    tags = _build_tags(programme, status, eligible_countries, total_pot_gbp or total_pot_eur)
    
    # Create Grant
    grant = Grant(
        grant_id=grant_id,
        source=GrantSource.EUREKA,
        external_id=grant_data.get('call_id'),
        title=title,
        url=url,
        status=status,
        is_active=is_active,
        sections=grant_sections,
        programme=programme_info,
        tags=tags,
        raw=None,
        processing=ProcessingInfo(
            scraped_at=_parse_date(raw.get('scraped_at')),
            normalized_at=_now(),
            sections_extracted=list(sections.keys()),
            schema_version="3.0",
        ),
        created_at=_now(),
        updated_at=_now(),
    )
    
    return grant


# =============================================================================
# SECTION BUILDERS
# =============================================================================

def _build_summary_section(about_text: str, programme: str) -> SummarySection:
    """Build summary section."""
    return SummarySection(
        text=about_text,
        call_type=programme,
        extracted_at=_now(),
    )


def _build_eligibility_section(
    eligibility_text: str,
    country_info: Dict[str, str],
    eligible_countries: List[str]
) -> EligibilitySection:
    """Build eligibility section with country info."""
    
    # Append country-specific info to eligibility text
    full_text = eligibility_text
    if country_info:
        country_text = "\n\n".join(f"{k}:\n{v}" for k, v in country_info.items() if isinstance(v, str))
        if country_text:
            full_text = f"{eligibility_text}\n\n{country_text}" if eligibility_text else country_text
    
    # Extract who can apply
    who_can_apply = _extract_who_can_apply(eligibility_text)
    
    # Check partnership requirements (Eureka always requires international partnership)
    partnership_required = True
    partnership_details = "International consortium with at least 2 partners from different Eureka countries"
    
    return EligibilitySection(
        text=full_text,
        who_can_apply=who_can_apply,
        eligible_countries=eligible_countries,
        geographic_scope="International (Eureka Network)",
        partnership_required=partnership_required,
        partnership_details=partnership_details,
        extracted_at=_now(),
    )


def _build_scope_section(about_text: str, funding_text: str, title: str) -> ScopeSection:
    """Build scope section with themes."""
    # Combine relevant text
    scope_text = funding_text if funding_text else about_text
    
    # Extract themes
    themes = _extract_themes(title, about_text)
    
    # Extract TRL
    trl_min, trl_max, trl_range = _extract_trl(about_text)
    
    return ScopeSection(
        text=scope_text,
        themes=themes,
        trl_min=trl_min,
        trl_max=trl_max,
        trl_range=trl_range,
        extracted_at=_now(),
    )


def _build_dates_section(
    opens_at: Optional[datetime],
    closes_at: Optional[datetime],
    key_dates_text: str
) -> DatesSection:
    """Build dates section."""
    # Extract deadline time from key_dates
    deadline_time = _extract_deadline_time(key_dates_text)
    
    # Extract project duration
    duration_min, duration_max, duration_text = _extract_project_duration(key_dates_text)
    
    return DatesSection(
        opens_at=opens_at,
        closes_at=closes_at,
        deadline_time=deadline_time,
        timezone="CET",  # Eureka typically uses CET
        project_duration=duration_text,
        project_duration_months_min=duration_min,
        project_duration_months_max=duration_max,
        key_dates_text=key_dates_text,
        extracted_at=_now(),
    )


def _build_funding_section(
    total_pot_gbp: Optional[int],
    total_pot_eur: Optional[int],
    total_pot_display: Optional[str],
    per_project_min: Optional[int],
    per_project_max: Optional[int],
    per_project_display: Optional[str],
    funding_text: str
) -> FundingSection:
    """Build funding section."""
    return FundingSection(
        text=funding_text,
        total_pot_gbp=total_pot_gbp,
        total_pot_eur=total_pot_eur,
        total_pot_display=total_pot_display,
        currency="EUR",  # Eureka primarily uses EUR
        per_project_min_gbp=per_project_min,
        per_project_max_gbp=per_project_max,
        per_project_display=per_project_display,
        competition_type=CompetitionType.GRANT,
        extracted_at=_now(),
    )


def _build_how_to_apply_section(how_to_apply_text: str) -> HowToApplySection:
    """Build how to apply section."""
    return HowToApplySection(
        text=how_to_apply_text,
        portal_name="Eureka Smartsimple Portal",
        portal_url="https://eureka.smartsimple.ie/",
        registration_required=True,
        extracted_at=_now(),
    )


def _build_assessment_section(how_to_apply_text: str, eligibility_text: str) -> AssessmentSection:
    """Build assessment section from how-to-apply content."""
    # Eureka standard criteria
    criteria = []
    combined = (how_to_apply_text + " " + eligibility_text).lower()
    
    criteria_patterns = [
        (r'\bimpact\b', 'Impact'),
        (r'\bexcellence\b', 'Excellence'),
        (r'\bquality\b.*\bimplementation\b', 'Quality of implementation'),
        (r'\binnovation\b', 'Innovation'),
        (r'\bcommerciali[sz]ation\b', 'Commercialisation potential'),
        (r'\bconsortium\b', 'Consortium quality'),
    ]
    
    for pattern, label in criteria_patterns:
        if re.search(pattern, combined):
            if label not in criteria:
                criteria.append(label)
    
    return AssessmentSection(
        text=_extract_assessment_text(how_to_apply_text),
        criteria=criteria,
        extracted_at=_now(),
    )


def _build_contacts_section(country_info: Dict[str, str]) -> ContactsSection:
    """Build contacts section."""
    # Extract emails from country info
    all_text = " ".join(str(v) for v in country_info.values() if v)
    emails = _extract_emails(all_text)
    
    contacts = [Contact(email=e) for e in emails]
    
    return ContactsSection(
        contacts=contacts,
        helpdesk_url="https://www.eurekanetwork.org/contact",
        extracted_at=_now(),
    )


def _build_programme_info(programme: str, call_id: str) -> ProgrammeInfo:
    """Build programme info."""
    return ProgrammeInfo(
        name=programme,
        funder="Eureka Network",
        eureka_programme=programme,
        code=call_id,
    )


# =============================================================================
# PARSING HELPERS
# =============================================================================

def _now() -> datetime:
    """Get current UTC time."""
    return datetime.now(timezone.utc)


def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse date string to datetime."""
    if not date_str:
        return None
    
    try:
        # Handle ISO format
        if 'T' in date_str:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        # Handle date only
        return datetime.strptime(date_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        return None


def _infer_status(status_str: Optional[str], opens_at: Optional[datetime], closes_at: Optional[datetime]) -> GrantStatus:
    """Infer grant status."""
    if status_str:
        status_lower = status_str.lower()
        if status_lower == 'open':
            return GrantStatus.OPEN
        elif status_lower == 'closed':
            return GrantStatus.CLOSED
        elif status_lower in ('upcoming', 'forthcoming'):
            return GrantStatus.FORTHCOMING
    
    # Fallback to date-based inference
    now = datetime.now(timezone.utc)
    
    if closes_at:
        closes_aware = closes_at.replace(tzinfo=timezone.utc) if closes_at.tzinfo is None else closes_at
        if closes_aware < now:
            return GrantStatus.CLOSED
    
    if opens_at:
        opens_aware = opens_at.replace(tzinfo=timezone.utc) if opens_at.tzinfo is None else opens_at
        if opens_aware > now:
            return GrantStatus.FORTHCOMING
    
    return GrantStatus.OPEN


def _get_funding_text(funding_section: Any) -> str:
    """Extract text from funding section (can be dict or string)."""
    if isinstance(funding_section, str):
        return funding_section
    elif isinstance(funding_section, dict):
        return funding_section.get('general', '') or str(funding_section)
    return ''


def _parse_funding_amount(funding_info: str, funding_text: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Parse total funding amount (returns GBP, EUR, display)."""
    combined = f"{funding_info} {funding_text}"
    
    # EUR patterns
    eur_patterns = [
        r'(\d+(?:[.,]\d+)?)\s*million\s*euro',
        r'€\s*(\d+(?:[.,]\d+)?)\s*million',
        r'(\d+(?:[.,]\d+)?)\s*m(?:illion)?\s*(?:euro|€|eur)',
        r'budget.*?(\d+(?:[.,]\d+)?)\s*million',
    ]
    
    for pattern in eur_patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            amount_str = match.group(1).replace(',', '.')
            amount = float(amount_str) * 1_000_000
            return None, int(amount), f"€{amount_str} million"
    
    # Specific amount patterns
    specific_patterns = [
        r'€\s*([\d,]+)\s*(?:euro)?',
        r'([\d,]+)\s*euro',
    ]
    
    for pattern in specific_patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            amount_str = match.group(1).replace(',', '')
            if len(amount_str) >= 4:  # At least 1000
                amount = int(amount_str)
                return None, amount, f"€{amount:,}"
    
    return None, None, None


def _parse_per_project_funding(funding_text: str, about_text: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Parse per-project funding."""
    combined = f"{funding_text} {about_text}"
    
    patterns = [
        # maximum of €X per project
        (r'maximum.*?€?\s*([\d,]+)\s*(?:euro|€)', 'max'),
        (r'up to €?\s*([\d,]+)\s*(?:euro|€)?\s*(?:per|each|for)', 'max'),
        # €X per project
        (r'€\s*([\d,]+)\s*(?:per project|each project|per market feasibility)', 'max'),
        # fixed grant of €X
        (r'fixed grant of €?\s*([\d,]+)', 'max'),
        # funding of €X
        (r'funding.*?€\s*([\d,]+)', 'max'),
        # CAD amounts
        (r'([\d,]+)\s*Canadian dollars', 'max_cad'),
    ]
    
    for pattern, ptype in patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            amount_str = match.group(1).replace(',', '')
            try:
                amount = int(amount_str)
                if ptype == 'max_cad':
                    # Convert CAD to EUR approximately (1 CAD ≈ 0.68 EUR)
                    amount_eur = int(amount * 0.68)
                    return None, amount_eur, f"up to ${amount:,} CAD (≈€{amount_eur:,})"
                else:
                    return None, amount, f"up to €{amount:,}"
            except ValueError:
                pass
    
    return None, None, None


def _extract_countries(country_info: Dict[str, str], title: str) -> List[str]:
    """Extract eligible countries from country_info and title."""
    countries = []
    
    # Common country patterns in Eureka
    country_patterns = {
        'canada': 'Canada',
        'chile': 'Chile',
        'japan': 'Japan',
        'korea': 'South Korea',
        'south korea': 'South Korea',
        'brazil': 'Brazil',
        'sweden': 'Sweden',
        'israel': 'Israel',
        'singapore': 'Singapore',
        'taiwan': 'Taiwan',
        'uk': 'United Kingdom',
        'united kingdom': 'United Kingdom',
        'germany': 'Germany',
        'france': 'France',
        'spain': 'Spain',
        'netherlands': 'Netherlands',
        'belgium': 'Belgium',
        'austria': 'Austria',
        'switzerland': 'Switzerland',
        'norway': 'Norway',
        'denmark': 'Denmark',
        'finland': 'Finland',
        'ireland': 'Ireland',
        'portugal': 'Portugal',
        'italy': 'Italy',
        'poland': 'Poland',
        'czech': 'Czech Republic',
        'hungary': 'Hungary',
        'turkey': 'Turkey',
        'türkiye': 'Turkey',
        'iceland': 'Iceland',
    }
    
    # Search title
    title_lower = title.lower()
    for pattern, country in country_patterns.items():
        if pattern in title_lower and country not in countries:
            countries.append(country)
    
    # Search country_info keys
    for key in country_info.keys():
        key_lower = key.lower()
        for pattern, country in country_patterns.items():
            if pattern in key_lower and country not in countries:
                countries.append(country)
    
    return countries


def _extract_who_can_apply(text: str) -> List[str]:
    """Extract who can apply from eligibility text."""
    who = []
    text_lower = text.lower()
    
    patterns = [
        (r'\bsme\b', 'SME'),
        (r'\bsmall.*medium', 'SME'),
        (r'\blarge enterprise', 'Large enterprise'),
        (r'\bresearch.*organi[sz]ation', 'Research organisation'),
        (r'\buniversit', 'University'),
        (r'\brto\b', 'RTO'),
    ]
    
    for pattern, label in patterns:
        if re.search(pattern, text_lower):
            if label not in who:
                who.append(label)
    
    return who


def _extract_themes(title: str, text: str) -> List[str]:
    """Extract themes from title and text."""
    themes = []
    combined = (title + " " + text).lower()
    
    theme_patterns = [
        (r'\bai\b|artificial intelligence|machine learning', 'AI & Machine Learning'),
        (r'\bnet zero\b|decarboni|climate|green|sustainable', 'Net Zero & Sustainability'),
        (r'\bhealth|medical|pharma|life science', 'Health & Life Sciences'),
        (r'\benergy|renewable|clean tech|battery', 'Energy & Clean Tech'),
        (r'\bmanufactur|industr', 'Manufacturing'),
        (r'\baerospace|aviation|space', 'Aerospace & Space'),
        (r'\bautomoti|vehicle|ev\b|mobility', 'Automotive & Mobility'),
        (r'\bdigital|cyber|software', 'Digital & Cyber'),
        (r'\bagricultur|agri|food', 'Agriculture & Food'),
        (r'\bmining|mineral', 'Mining & Resources'),
        (r'\bconstruction|building', 'Construction'),
        (r'\bquantum', 'Quantum'),
    ]
    
    for pattern, label in theme_patterns:
        if re.search(pattern, combined):
            if label not in themes:
                themes.append(label)
    
    return themes


def _extract_trl(text: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Extract TRL range from text."""
    # Pattern: TRL X-Y or TRL X to Y
    pattern = r'trl\s*(?:level\s*)?(\d)\s*[-–to]+\s*(?:trl\s*(?:level\s*)?)?(\d)'
    match = re.search(pattern, text.lower())
    
    if match:
        trl_min = int(match.group(1))
        trl_max = int(match.group(2))
        return trl_min, trl_max, f"TRL {trl_min}-{trl_max}"
    
    # Single TRL mention
    pattern = r'trl\s*(?:level\s*)?(\d)'
    match = re.search(pattern, text.lower())
    
    if match:
        trl = int(match.group(1))
        return trl, None, f"TRL {trl}+"
    
    return None, None, None


def _extract_deadline_time(text: str) -> Optional[str]:
    """Extract deadline time from key_dates text."""
    # Pattern: 23:59, 17:59, etc.
    pattern = r'(\d{1,2}:\d{2})\s*(CET|CEST|GMT|UTC)?'
    match = re.search(pattern, text)
    
    if match:
        time = match.group(1)
        tz = match.group(2) or 'CET'
        return f"{time} {tz}"
    
    return None


def _extract_project_duration(text: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Extract project duration from text."""
    # Pattern: X to Y months
    pattern = r'(\d+)\s*(?:to|-)\s*(\d+)\s*months?'
    match = re.search(pattern, text.lower())
    
    if match:
        min_months = int(match.group(1))
        max_months = int(match.group(2))
        return min_months, max_months, f"{min_months}-{max_months} months"
    
    # Pattern: up to X months or maximum X months
    pattern = r'(?:up to|maximum|max)\s*(\d+)\s*months?'
    match = re.search(pattern, text.lower())
    
    if match:
        max_months = int(match.group(1))
        return None, max_months, f"up to {max_months} months"
    
    # Pattern: X months duration
    pattern = r'(\d+)\s*months?\s*(?:duration)?'
    match = re.search(pattern, text.lower())
    
    if match:
        months = int(match.group(1))
        return months, months, f"{months} months"
    
    return None, None, None


def _extract_assessment_text(how_to_apply_text: str) -> Optional[str]:
    """Extract assessment criteria text."""
    # Look for numbered criteria sections
    patterns = [
        r'(1\.\s*Impact.*?(?=\n\n|\Z))',
        r'(assessment.*?criteria.*?(?=\n\n|\Z))',
        r'(evaluation.*?criteria.*?(?=\n\n|\Z))',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, how_to_apply_text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    
    return None


def _extract_emails(text: str) -> List[str]:
    """Extract email addresses from text."""
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(pattern, text)
    
    # Remove duplicates
    seen = set()
    unique = []
    for email in emails:
        email_lower = email.lower()
        if email_lower not in seen:
            seen.add(email_lower)
            unique.append(email)
    
    return unique


def _build_tags(
    programme: str,
    status: GrantStatus,
    countries: List[str],
    total_amount: Optional[int]
) -> List[str]:
    """Build tags for filtering."""
    tags = ["eureka_network"]
    
    # Programme
    if programme:
        tags.append(programme.lower().replace(' ', '_'))
    
    # Status
    tags.append(status.value)
    
    # International
    tags.append("international")
    
    # Countries
    for country in countries[:3]:  # Limit to first 3
        tags.append(country.lower().replace(' ', '_'))
    
    # Funding size
    if total_amount:
        if total_amount >= 1_000_000:
            tags.append("large_fund")
        elif total_amount >= 100_000:
            tags.append("medium_fund")
        else:
            tags.append("small_fund")
    
    return tags


# =============================================================================
# BATCH PROCESSING
# =============================================================================

def normalize_eureka_batch(grants_data: List[Dict[str, Any]]) -> List[Grant]:
    """
    Normalize a batch of Eureka grants.
    
    Args:
        grants_data: List of grant dicts from normalized.json
        
    Returns:
        List of normalized Grants
    """
    grants = []
    
    for i, data in enumerate(grants_data):
        try:
            grant = normalize_eureka_v3(data)
            grants.append(grant)
        except Exception as e:
            logger.error(f"Failed to normalize {data.get('title', 'Unknown')}: {e}")
    
    return grants


def load_and_normalize(json_path: str = "data/eureka_network/normalized.json") -> List[Grant]:
    """
    Load grants from JSON and normalize to v3.
    
    Args:
        json_path: Path to normalized.json
        
    Returns:
        List of normalized Grants
    """
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {json_path}")
    
    with open(path) as f:
        grants_data = json.load(f)
    
    return normalize_eureka_batch(grants_data)


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import sys
    
    json_path = sys.argv[1] if len(sys.argv) > 1 else "data/eureka_network/normalized.json"
    
    print(f"Loading from: {json_path}")
    grants = load_and_normalize(json_path)
    
    print(f"\n{'='*60}")
    print(f"NORMALIZED {len(grants)} EUREKA GRANTS")
    print(f"{'='*60}")
    
    # Summary stats
    open_count = sum(1 for g in grants if g.status == GrantStatus.OPEN)
    closed_count = sum(1 for g in grants if g.status == GrantStatus.CLOSED)
    
    print(f"\nStatus: {open_count} open, {closed_count} closed")
    
    # Section coverage
    print(f"\nSection coverage:")
    print(f"  Summary:     {sum(1 for g in grants if g.sections.summary.text)}/{len(grants)}")
    print(f"  Eligibility: {sum(1 for g in grants if g.sections.eligibility.text)}/{len(grants)}")
    print(f"  Scope:       {sum(1 for g in grants if g.sections.scope.text)}/{len(grants)}")
    print(f"  Dates:       {sum(1 for g in grants if g.sections.dates.opens_at or g.sections.dates.closes_at)}/{len(grants)}")
    print(f"  Funding:     {sum(1 for g in grants if g.sections.funding.text)}/{len(grants)}")
    print(f"  How to Apply:{sum(1 for g in grants if g.sections.how_to_apply.text)}/{len(grants)}")
    
    # Sample output
    print(f"\n{'='*60}")
    print("SAMPLE GRANTS:")
    print(f"{'='*60}")
    
    for grant in grants[:3]:
        print(f"\n{grant.title}")
        print(f"  ID: {grant.grant_id}")
        print(f"  Status: {grant.status.value}")
        print(f"  Programme: {grant.programme.eureka_programme}")
        print(f"  Countries: {grant.sections.eligibility.eligible_countries}")
        print(f"  Funding: {grant.sections.funding.per_project_display}")
        print(f"  Themes: {grant.sections.scope.themes}")
