"""
build_screener_profiles.py
==========================
Scrapes company descriptions, management/leadership, sector, industry,
and key financial ratios from Screener.in for companies listed in companies.json.
Saves results into screener_profiles/{SYMBOL}.json.

Usage:
    python build_screener_profiles.py --symbol DELTACORP.BO
    python build_screener_profiles.py --all
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

# Add screener-scraper folder to sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRAPER_DIR = os.path.join(BASE_DIR, "screener-scraper")
if SCRAPER_DIR not in sys.path:
    sys.path.insert(0, SCRAPER_DIR)

from screenerScraper import ScreenerScrape

COMPANIES_FILE = os.path.join(BASE_DIR, "companies.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "screener_profiles")
PAUSE_BETWEEN_REQUESTS = 2  # seconds


def load_companies():
    if not os.path.exists(COMPANIES_FILE):
        raise FileNotFoundError(f"Companies file not found at {COMPANIES_FILE}")
    with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def scrape_company_profile(ticker_symbol: str, company_name: str = ""):
    """
    Extracts description, management, sector, industry, and key ratios from Screener.in.
    """
    scraper = ScreenerScrape()
    try:
        scraper.loadScraper(ticker_symbol, consolidated=True)
    except Exception as e:
        # Retry with standalone if consolidated failed
        try:
            scraper.loadScraper(ticker_symbol, consolidated=False)
        except Exception as e2:
            return None, f"Failed to load Screener page: {e2}"

    try:
        profile_data = scraper.getCompanyProfile()
        # Add company metadata
        profile_data["ticker"] = ticker_symbol
        if company_name:
            profile_data["company_name"] = company_name
        return profile_data, None
    except Exception as e:
        return None, f"Failed to parse profile data: {e}"


def save_profile(ticker_symbol: str, data: dict):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # Primary requested filename format: screener_profiles/{SYMBOL}.json
    file_path = os.path.join(OUTPUT_DIR, f"{ticker_symbol}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    # Also save with underscore format (e.g. DELTACORP_BO.json) for consistency with profiles/
    safe_symbol = ticker_symbol.replace(".", "_")
    if safe_symbol != ticker_symbol:
        alt_path = os.path.join(OUTPUT_DIR, f"{safe_symbol}.json")
        with open(alt_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    return file_path


def run_single(ticker_symbol: str, companies: dict):
    print(f"\nProcessing {ticker_symbol}...")
    company_name = companies.get(ticker_symbol, "")
    data, err = scrape_company_profile(ticker_symbol, company_name)
    if err:
        print(f"  [ERROR] {err}")
        return False

    file_path = save_profile(ticker_symbol, data)
    print(f"  [SUCCESS] Profile saved to {file_path}")
    print(f"  - Sector       : {data.get('sector')}")
    print(f"  - Industry     : {data.get('industry')}")
    print(f"  - Management   : {data.get('management')}")
    ratios = data.get('ratios', {})
    print(f"  - Ratios       : ROE={ratios.get('roe')}, ROCE={ratios.get('roce')}, D/E={ratios.get('debt_to_equity')}, Current Ratio={ratios.get('current_ratio')}")
    desc_preview = data.get('description', '')[:120].replace('\n', ' ')
    print(f"  - Description  : {desc_preview}...")
    return True


def run_all(companies: dict):
    total = len(companies)
    success_count = 0
    failed_list = []

    print(f"\nScraping Screener profiles for all {total} companies...")
    print(f"Saving to directory: {OUTPUT_DIR}\n")

    for idx, (ticker, name) in enumerate(companies.items(), start=1):
        print(f"[{idx}/{total}] Fetching {ticker} ({name})...", end=" ", flush=True)
        data, err = scrape_company_profile(ticker, name)
        if data:
            save_profile(ticker, data)
            success_count += 1
            print("OK")
        else:
            failed_list.append((ticker, err))
            print(f"FAILED ({err})")

        if idx < total:
            time.sleep(PAUSE_BETWEEN_REQUESTS)

    print("\n" + "=" * 50)
    print("EXTRACTION SUMMARY:")
    print(f"  Total companies  : {total}")
    print(f"  Successful       : {success_count}")
    print(f"  Failed           : {len(failed_list)}")
    if failed_list:
        print("  Failed companies:")
        for t, e in failed_list:
            print(f"    - {t}: {e}")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Scrape company profiles from Screener.in")
    parser.add_argument("--symbol", type=str, help="Single company ticker (e.g. DELTACORP.BO)")
    parser.add_argument("--all", action="store_true", help="Scrape all 27 companies in companies.json")

    args = parser.parse_args()

    companies = load_companies()

    if args.symbol:
        sym = args.symbol.strip().upper()
        ok = run_single(sym, companies)
        sys.exit(0 if ok else 1)
    elif args.all:
        run_all(companies)
    else:
        print("Please provide --symbol <TICKER> or --all")
        parser.print_help()


if __name__ == "__main__":
    main()
