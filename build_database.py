"""
build_database.py
=================
Aggregates company data from:
  - companies.json (27 ticker symbols and supplied names)
  - profiles/ (Yahoo Finance profiles via yfinance)
  - screener_profiles/ (Screener.in profiles with sector, industry, description, ratios)
  - research_profiles/ (In-depth research profiles for DELTACORP.BO, PPAP.BO, MATRIMONY.BO)

Creates company_database.json, a single unified reference file for matching news events to companies.

Rules:
  - Keep Yahoo, Screener, and research data in separate fields; never merge them.
  - Missing data = "unavailable". Never invent names, numbers, or relationships.
  - Sourced leadership, dependencies, customers, suppliers, and partners only.
  - Sourced match_keywords strictly derived from company names, ticker, brands, and leaders.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANIES_FILE = os.path.join(BASE_DIR, "companies.json")
PROFILES_DIR = os.path.join(BASE_DIR, "profiles")
SCREENER_DIR = os.path.join(BASE_DIR, "screener_profiles")
RESEARCH_DIR = os.path.join(BASE_DIR, "research_profiles")
OUTPUT_FILE = os.path.join(BASE_DIR, "company_database.json")

UNAVAILABLE = "unavailable"


def clean_leader_name(raw_name: str) -> str:
    """Removes honorific prefixes (Mr., Dr., etc.) and normalizes whitespace."""
    if not raw_name or not isinstance(raw_name, str):
        return ""
    cleaned = re.sub(r"^(?:Mr\.|Mrs\.|Ms\.|Dr\.|Shri|Smt\.)\s+", "", raw_name, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def extract_brands(yahoo_desc: str, screener_desc: str, research_data: dict) -> list:
    """Extracts verified brands and products explicitly mentioned in descriptions or research."""
    brands = []

    # 1. Brands from research profile offerings / text
    if isinstance(research_data, dict):
        offerings = research_data.get("products_and_services", {}).get("offerings", [])
        for off in offerings:
            name = off.get("name")
            if name:
                brands.append(name)

        combined_research_text = json.dumps(research_data.get("products_and_services", {}))
        # Specific known product lines explicitly listed in research
        for brand_pat in [
            r"Deltin\s+[A-Za-z]+", r"The Deltin", r"Adda52", r"Adda\.games", r"King Casino",
            r"BharatMatrimony", r"TamilMatrimony", r"TeluguMatrimony", r"EliteMatrimony",
            r"AssistedMatrimony", r"Jodii", r"WeddingBazaar", r"Mandap\.com", r"WeddingMandaps",
            r"PPAP Technology", r"P-Tech"
        ]:
            matches = re.findall(rf"\b(?:{brand_pat})\b", combined_research_text, flags=re.IGNORECASE)
            for m in matches:
                if m not in brands:
                    brands.append(m)

    # 2. Brands from description patterns (e.g. 'under the ... brand(s)')
    desc_text = f"{yahoo_desc or ''} {screener_desc or ''}"
    if desc_text.strip():
        # Look for "under the X, Y and Z brands"
        pattern = r"under the ([A-Za-z0-9,\s&]+?) brands?"
        matches = re.findall(pattern, desc_text, flags=re.IGNORECASE)
        for group in matches:
            parts = re.split(r",|\band\b", group)
            for p in parts:
                cleaned_p = p.strip()
                if cleaned_p and len(cleaned_p) > 2 and cleaned_p.lower() not in ["the", "other", "various"]:
                    if cleaned_p not in brands:
                        brands.append(cleaned_p)

    return brands


def build_match_keywords(
    ticker: str,
    supplied_name: str,
    company_name: str,
    leadership: list,
    brands: list
) -> list:
    """
    Generates high-precision matching keywords strictly sourced from:
      - Ticker symbol (with and without exchange suffix)
      - Supplied and verified company names
      - Common corporate root variants (removing Ltd, Limited, Corp, etc.)
      - Clean leader names
      - Explicitly documented brand / subsidiary names
    """
    keywords = []

    # Ticker variations
    keywords.append(ticker)
    clean_sym = ticker.split(".")[0]
    if clean_sym not in keywords:
        keywords.append(clean_sym)

    # Name variations
    for name in [supplied_name, company_name]:
        if name and name != UNAVAILABLE:
            if name not in keywords:
                keywords.append(name)

            # Strip standard corporate legal endings
            root_name = re.sub(
                r"(?i)\s+(?:limited|ltd\.?|private|pvt\.?|corporation|corp\.?|industries|ind\.?|enterprises|holdings|services|company|co\.?)$",
                "",
                name
            ).strip()
            if root_name and len(root_name) > 3 and root_name not in keywords:
                keywords.append(root_name)

            # Fused / single-word variant for compound names (e.g. DeltaCorp, VinylIndia)
            fused = re.sub(r"[\s\.\&]+", "", root_name)
            if len(fused) > 4 and fused not in keywords and not fused.isdigit():
                keywords.append(fused)

    # Leader names
    if isinstance(leadership, list):
        for leader in leadership:
            raw_name = leader.get("name", "") if isinstance(leader, dict) else str(leader)
            cleaned = clean_leader_name(raw_name)
            if cleaned and len(cleaned) > 3:
                if cleaned not in keywords:
                    keywords.append(cleaned)
                parts = cleaned.split()
                if len(parts) == 3:
                    first_last = f"{parts[0]} {parts[2]}"
                    if first_last not in keywords:
                        keywords.append(first_last)

    # Brand names
    for b in brands:
        if b and b != UNAVAILABLE and b not in keywords:
            keywords.append(b)

    # Deduplicate case-insensitively while preserving order
    seen = set()
    deduped = []
    for kw in keywords:
        kw_str = str(kw).strip()
        kw_lower = kw_str.lower()
        if kw_str and kw_lower not in seen:
            seen.add(kw_lower)
            deduped.append(kw_str)

    return deduped


def build_database():
    if not os.path.exists(COMPANIES_FILE):
        print(f"ERROR: Cannot find {COMPANIES_FILE}")
        sys.exit(1)

    with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
        companies_map = json.load(f)

    total_count = len(companies_map)
    with_research_count = 0
    with_yahoo_only_count = 0
    failed_count = 0

    database = {}

    for ticker, supplied_name in companies_map.items():
        safe_sym = ticker.replace(".", "_")

        # 1. Load Yahoo profile
        yahoo_path = os.path.join(PROFILES_DIR, f"{safe_sym}.json")
        yahoo_data = None
        yahoo_retrieval_date = UNAVAILABLE
        if os.path.exists(yahoo_path):
            try:
                with open(yahoo_path, "r", encoding="utf-8") as yf:
                    yahoo_data = json.load(yf)
                yahoo_retrieval_date = yahoo_data.get("_meta", {}).get("retrieval_timestamp_utc", UNAVAILABLE)
            except Exception as e:
                print(f"Warning: Failed to parse Yahoo profile {yahoo_path}: {e}")

        # 2. Load Screener profile
        screener_path = os.path.join(SCREENER_DIR, f"{ticker}.json")
        if not os.path.exists(screener_path):
            screener_path = os.path.join(SCREENER_DIR, f"{safe_sym}.json")
        screener_data = None
        screener_retrieval_date = UNAVAILABLE
        if os.path.exists(screener_path):
            try:
                with open(screener_path, "r", encoding="utf-8") as sf:
                    screener_data = json.load(sf)
                screener_retrieval_date = screener_data.get("extraction_date", UNAVAILABLE)
            except Exception as e:
                print(f"Warning: Failed to parse Screener profile {screener_path}: {e}")

        # 3. Load Research profile
        research_path = os.path.join(RESEARCH_DIR, f"{safe_sym}.json")
        if not os.path.exists(research_path):
            research_path = os.path.join(RESEARCH_DIR, f"{ticker}.json")
        research_data = None
        research_retrieval_date = UNAVAILABLE
        if os.path.exists(research_path):
            try:
                with open(research_path, "r", encoding="utf-8") as rf:
                    research_data = json.load(rf)
                research_retrieval_date = research_data.get("_meta", {}).get("research_date_utc", UNAVAILABLE)
            except Exception as e:
                print(f"Warning: Failed to parse Research profile {research_path}: {e}")

        if not yahoo_data and not screener_data:
            failed_count += 1
            print(f"ERROR: Failed to load both Yahoo and Screener profiles for {ticker}")
            continue

        if research_data:
            with_research_count += 1
        else:
            with_yahoo_only_count += 1

        # Determine official/verified company name
        company_name = UNAVAILABLE
        if research_data and research_data.get("_meta", {}).get("verified_name"):
            company_name = research_data["_meta"]["verified_name"]
        elif yahoo_data and yahoo_data.get("_meta", {}).get("yahoo_long_name") and yahoo_data["_meta"]["yahoo_long_name"] != UNAVAILABLE:
            company_name = yahoo_data["_meta"]["yahoo_long_name"]
        elif yahoo_data and yahoo_data.get("_meta", {}).get("yahoo_short_name") and yahoo_data["_meta"]["yahoo_short_name"] != UNAVAILABLE:
            company_name = yahoo_data["_meta"]["yahoo_short_name"]
        elif screener_data and screener_data.get("company_name"):
            company_name = screener_data["company_name"]
        else:
            company_name = supplied_name

        # Sector and Industry
        sector = UNAVAILABLE
        industry = UNAVAILABLE
        if screener_data and screener_data.get("sector") and screener_data["sector"] != UNAVAILABLE:
            sector = screener_data["sector"]
        elif yahoo_data and yahoo_data.get("description", {}).get("sector"):
            sector = yahoo_data["description"]["sector"]

        if screener_data and screener_data.get("industry") and screener_data["industry"] != UNAVAILABLE:
            industry = screener_data["industry"]
        elif yahoo_data and yahoo_data.get("description", {}).get("industry"):
            industry = yahoo_data["description"]["industry"]

        # Descriptions
        yahoo_description = UNAVAILABLE
        if yahoo_data and yahoo_data.get("description", {}).get("summary"):
            yahoo_description = yahoo_data["description"]["summary"]

        screener_description = UNAVAILABLE
        if screener_data and screener_data.get("description"):
            screener_description = screener_data["description"]

        # Screener ratios (keep unchanged)
        screener_ratios = UNAVAILABLE
        if screener_data and screener_data.get("ratios"):
            screener_ratios = screener_data["ratios"]

        # Research summary (from research only)
        research_summary = UNAVAILABLE
        if research_data:
            business_model = research_data.get("products_and_services", {}).get("business_model")
            if business_model:
                research_summary = business_model

        # Leadership (name and title from source only)
        leadership = UNAVAILABLE
        if yahoo_data and yahoo_data.get("leadership"):
            raw_lead = yahoo_data["leadership"]
            if isinstance(raw_lead, list) and raw_lead:
                lead_list = []
                for item in raw_lead:
                    if isinstance(item, dict) and item.get("name"):
                        lead_list.append({
                            "name": item["name"],
                            "title": item.get("title", UNAVAILABLE)
                        })
                if lead_list:
                    leadership = lead_list

        # Research-only fields: key_dependencies, named_customers, named_suppliers, named_partners
        key_dependencies = UNAVAILABLE
        named_customers = UNAVAILABLE
        named_suppliers = UNAVAILABLE
        named_partners = UNAVAILABLE

        if research_data:
            if "business_dependencies" in research_data:
                key_dependencies = research_data["business_dependencies"]

            if "named_customers" in research_data:
                named_customers = research_data["named_customers"]

            # Separate suppliers and partners from named_suppliers_and_partners
            supp_partners = research_data.get("named_suppliers_and_partners", [])
            if isinstance(supp_partners, list):
                suppliers_list = []
                partners_list = []
                for sp in supp_partners:
                    rel = str(sp.get("relation_type", "")).lower()
                    if "supplier" in rel:
                        suppliers_list.append(sp)
                    else:
                        partners_list.append(sp)
                named_suppliers = suppliers_list if suppliers_list else UNAVAILABLE
                named_partners = partners_list if partners_list else UNAVAILABLE

        # Extract brands and build match keywords
        brands = extract_brands(yahoo_description, screener_description, research_data)
        match_keywords = build_match_keywords(ticker, supplied_name, company_name, leadership, brands)

        # Build source files dictionary
        source_files = {
            "yahoo_profile": {
                "path": os.path.relpath(yahoo_path, BASE_DIR).replace("\\", "/") if os.path.exists(yahoo_path) else UNAVAILABLE,
                "retrieval_date": yahoo_retrieval_date
            },
            "screener_profile": {
                "path": os.path.relpath(screener_path, BASE_DIR).replace("\\", "/") if os.path.exists(screener_path) else UNAVAILABLE,
                "retrieval_date": screener_retrieval_date
            },
            "research_profile": {
                "path": os.path.relpath(research_path, BASE_DIR).replace("\\", "/") if research_data else UNAVAILABLE,
                "retrieval_date": research_retrieval_date if research_data else UNAVAILABLE
            } if research_data else UNAVAILABLE
        }

        # Build full company record
        database[ticker] = {
            "ticker": ticker,
            "supplied_name": supplied_name,
            "company_name": company_name,
            "sector": sector,
            "industry": industry,
            "yahoo_description": yahoo_description,
            "screener_description": screener_description,
            "screener_ratios": screener_ratios,
            "research_summary": research_summary,
            "leadership": leadership,
            "key_dependencies": key_dependencies,
            "named_customers": named_customers,
            "named_suppliers": named_suppliers,
            "named_partners": named_partners,
            "match_keywords": match_keywords,
            "source_files": source_files
        }

    # Save to company_database.json
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        json.dump(database, out, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("COMPANY DATABASE GENERATION REPORT")
    print("=" * 60)
    print(f"Total companies in companies.json : {total_count}")
    print(f"Total entries created in database : {len(database)}")
    print(f"Companies with in-depth research : {with_research_count}")
    print(f"Companies without research        : {with_yahoo_only_count}")
    print(f"Failed to read                    : {failed_count}")
    print(f"Output saved to                   : {OUTPUT_FILE}")
    print("=" * 60)

    # Validation
    missing_tickers = [t for t in companies_map if t not in database]
    empty_names = [t for t, d in database.items() if not d.get("company_name") or d.get("company_name") == UNAVAILABLE]

    print("\nVALIDATION CHECKS:")
    print(f"  All 27 tickers present          : {'PASSED' if not missing_tickers else f'FAILED ({missing_tickers})'}")
    print(f"  Company name not empty for all  : {'PASSED' if not empty_names else f'FAILED ({empty_names})'}")

    return database


if __name__ == "__main__":
    db = build_database()
