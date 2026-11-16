"""
match_news.py
=============
Processes market news headlines from Economic Times RSS (as used in news.py),
scores events, groups stories strictly by shared underlying event or named entity,
and matches articles against all 27 companies in company_database.json.

Key Standards Enforced:
  1. Distinguishes US Fed / US rates from RBI / India domestic rates.
  2. Preserves geography (US, India, Global) and relevant institution (Fed, RBI, etc.).
  3. Groups articles only when they concern the same underlying event or named entity
     (e.g., Anthropic IPO and Bajaj Finance warrants are strictly separated).
  4. Three distinct match types:
     - "explicit_mention": company, ticker, or verified brand appears in text.
     - "verified_relationship": named customer, supplier, or partner from research profile appears (with source).
     - "sector_hypothesis": plausible sector transmission, clearly marked as hypothesis with missing connection noted.
  5. Includes exact trigger_phrase and database_field used.
  6. Does not attach Indian banks/HFCs to US Fed stories as verified matches.
  7. Includes offline test suite (--test).
  8. Output saved to matched_news.json. No Ollama used.
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone

# Add screener-scraper to path if needed
sys.path.append(os.path.abspath("screener-scraper"))

import httpx
import trafilatura

# ==============================================================================
# CONFIG & PATHS
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "company_database.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "matched_news.json")
RSS_URL = "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"
MAX_HEADLINES = 10
REQ_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Generic common words to prevent false-positive dictionary matching
COMMON_GUARD_WORDS = {
    "legend", "genius", "hunter", "power", "cool", "apollo", "bison",
    "panel", "milestone", "white", "black", "gold", "blue", "rare", "super",
    "services", "company", "limited", "india", "international"
}

# Scoring rules (from news.py)
HIGH_KEYWORDS = [
    "rbi", "fed", "crash", "plunge", "soar", "surge", "merger",
    "acquisition", "acquire", "profit", "loss", "default",
    "bankrupt", "inflation", "rate hike", "rate cut", "repo"
]
MEDIUM_KEYWORDS = [
    "earnings", "revenue", "growth", "decline", "upgrade",
    "downgrade", "ipo", "dividend", "quarterly", "results", "q1",
    "q2", "q3", "q4", "warrants", "promoter"
]
LOW_KEYWORDS = [
    "52-week", "technical", "chart", "live update", "nifty",
    "sensex", "trade setup", "overtakes"
]


# ==============================================================================
# 1. SCORING & GEOGRAPHY / INSTITUTION CLASSIFICATION
# ==============================================================================
def score_headline(title: str) -> int:
    """Calculates importance score according to standard rules."""
    t = title.lower()
    score = 0
    for kw in HIGH_KEYWORDS:
        if kw in t:
            score += 3
    for kw in MEDIUM_KEYWORDS:
        if kw in t:
            score += 2
    for kw in LOW_KEYWORDS:
        if kw in t:
            score += 1
    return max(score, 1)


def classify_geography_and_institution(title: str, description: str = "") -> dict:
    """
    Identifies geography (US, India, Europe, Global), relevant institution (Fed, RBI),
    and whether a direct India connection is established in the text.
    """
    text = f"{title} {description}".lower()

    # Institution
    institution = "None"
    if re.search(r"\b(rbi|reserve bank of india|shaktikanta das|mpc)\b", text):
        institution = "Reserve Bank of India"
    elif re.search(r"\b(fed|federal reserve|fomc|jerome powell|waller)\b", text):
        institution = "US Federal Reserve"
    elif re.search(r"\b(ecb|european central bank|lagarde)\b", text):
        institution = "European Central Bank"

    # Geography
    has_us_context = bool(re.search(r"\b(fed|wall street|dow jones|nasdaq|us |u\.s\.|united states|treasury|cpi|dollar)\b", text))
    has_india_context = bool(re.search(r"\b(rbi|india|indian|nifty|sensex|sebi|bse|nse|d-street|rupee|rs |crore|lakh|bajaj)\b", text))
    has_europe_context = bool(re.search(r"\b(euro|europe|eurozone|ecb|london|ftse)\b", text))

    if has_india_context and not has_us_context and not has_europe_context:
        geography = "India"
    elif has_us_context and not has_india_context:
        geography = "US"
    elif has_europe_context and not has_india_context and not has_us_context:
        geography = "Europe"
    elif has_india_context and has_us_context:
        geography = "US / India Cross-Market"
    else:
        geography = "Global"

    # India connection established?
    india_connection_established = has_india_context

    return {
        "geography": geography,
        "relevant_institution": institution,
        "india_connection_established": india_connection_established
    }


# ==============================================================================
# 2. STORY GROUPING (Strictly by Event / Named Entity)
# ==============================================================================
def extract_event_entity(title: str) -> str | None:
    """Extracts prominent named entities to avoid cross-company false grouping."""
    t_lower = title.lower()

    # Specific known companies / organizations
    named_entities = [
        ("anthropic", "Anthropic"),
        ("bajaj finance", "Bajaj Finance"),
        ("nvidia", "Nvidia"),
        ("microsoft", "Microsoft"),
        ("tsmc", "TSMC"),
        ("openai", "OpenAI"),
        ("trade desk", "The Trade Desk"),
        ("rocket lab", "Rocket Lab"),
        ("delta corp", "Delta Corp"),
        ("maruti suzuki", "Maruti Suzuki"),
        ("dcb bank", "DCB Bank"),
        ("repco home", "Repco Home Finance")
    ]
    for pattern, name in named_entities:
        if pattern in t_lower:
            return name

    return None


def determine_story_group(title: str, description: str = "") -> str:
    """
    Groups articles ONLY when they concern the same underlying event or named entity.
    Never groups unrelated companies together simply because both involve 'IPO' or 'warrants'.
    """
    t_lower = title.lower()
    text_lower = f"{title} {description}".lower()

    # 1. Check specific named corporate entity first
    entity = extract_event_entity(title)
    if entity == "Anthropic":
        return "Anthropic AI & IPO"
    if entity == "Bajaj Finance":
        return "Bajaj Finance Capital Action"
    if entity in ["Nvidia", "Microsoft"]:
        # If both are in Nvidia/Microsoft tech market context
        return "Big Tech & Semiconductor Valuation"
    if entity == "TSMC":
        return "TSMC AI Ecosystem Investment"
    if entity:
        return f"Company: {entity}"

    # 2. Institutional macro events (must cleanly separate US Fed from RBI)
    if re.search(r"\b(rbi|repo rate|monetary policy committee)\b", t_lower):
        return "RBI / Domestic Monetary Policy"

    if re.search(r"\b(fed|federal reserve|fomc|treasury yield|waller)\b", t_lower) and "rbi" not in t_lower:
        return "US Fed / US Monetary Policy"

    if re.search(r"\b(cpi|consumer prices|inflation)\b", t_lower):
        if "us" in t_lower or "dow jones" in t_lower or "wall street" in t_lower:
            return "US Inflation & Consumer Prices"
        return "Global / Regional Inflation"

    if re.search(r"\b(crude|oil|opec|brent)\b", t_lower):
        return "Crude Oil & Energy Markets"

    if re.search(r"\b(fpi|foreign portfolio|fii)\b", t_lower):
        return "Foreign Institutional Flows (FPI)"

    # Fallback standalone
    return "Market Standalone"


def build_event_groups(items):
    """
    Groups items by specific underlying event or named entity.
    Returns list of groups and list of single items.
    """
    cluster_map = defaultdict(list)
    for it in items:
        grp = determine_story_group(it["title"], it.get("description", ""))
        cluster_map[grp].append(it)

    groups = []
    singles = []

    for label, group_items in cluster_map.items():
        if len(group_items) == 1 or label == "Market Standalone":
            for it in group_items:
                it["group"] = label
            singles.extend(group_items)
        else:
            for it in group_items:
                it["group"] = label
            total_score = sum(i["score"] for i in group_items)
            group_items.sort(key=lambda x: x["score"], reverse=True)
            groups.append({
                "label": label,
                "score": total_score,
                "items": group_items
            })

    groups.sort(key=lambda g: g["score"], reverse=True)
    singles.sort(key=lambda s: s["score"], reverse=True)
    return groups, singles


# ==============================================================================
# 3. MATCHING LOGIC (Explicit Mention, Verified Relationship, Sector Hypothesis)
# ==============================================================================
def load_database():
    if not os.path.exists(DB_FILE):
        raise FileNotFoundError(f"Cannot find company database at {DB_FILE}")
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def match_article_to_companies(title: str, description: str, content: str, db: dict, geo_info: dict) -> list:
    """
    Evaluates article against company_database.json and returns a list of match records.
    Every match record contains:
      - ticker
      - company_name
      - match_type ("explicit_mention" | "verified_relationship" | "sector_hypothesis")
      - trigger_phrase (exact substring found in article)
      - database_field (field in company_database.json)
      - details (relationship, source, or hypothesis with missing connection)
    """
    full_text = f"{title} {description} {content}"
    full_text_lower = full_text.lower()
    title_lower = title.lower()

    matches = []

    for ticker, cdata in db.items():
        company_name = cdata.get("company_name", ticker)
        supplied_name = cdata.get("supplied_name", "")

        # -------------------------------------------------------------
        # 1. EXPLICIT MENTION
        # Company name, ticker, verified brand/product, or executive
        # -------------------------------------------------------------
        explicit_found = False

        # Ticker check
        clean_sym = ticker.split(".")[0]
        if len(clean_sym) >= 3:
            # Require exact uppercase word boundary for short tickers
            if len(clean_sym) <= 4:
                m_sym = re.search(rf"\b{re.escape(clean_sym)}\b", full_text)
                if m_sym:
                    matches.append({
                        "ticker": ticker,
                        "company_name": company_name,
                        "match_type": "explicit_mention",
                        "trigger_phrase": m_sym.group(0),
                        "database_field": "ticker",
                        "details": f"Article explicitly mentions stock ticker symbol '{clean_sym}'."
                    })
                    explicit_found = True
            else:
                m_sym = re.search(rf"\b{re.escape(clean_sym)}\b", full_text, flags=re.IGNORECASE)
                if m_sym:
                    matches.append({
                        "ticker": ticker,
                        "company_name": company_name,
                        "match_type": "explicit_mention",
                        "trigger_phrase": m_sym.group(0),
                        "database_field": "ticker",
                        "details": f"Article explicitly mentions ticker/identifier '{clean_sym}'."
                    })
                    explicit_found = True

        # Company name & root name check
        if not explicit_found:
            for name_candidate in [company_name, supplied_name]:
                if name_candidate and name_candidate != "unavailable":
                    root_name = re.sub(
                        r"(?i)\s+(?:limited|ltd\.?|private|pvt\.?|corporation|corp\.?|industries|ind\.?|enterprises|holdings|services)$",
                        "",
                        name_candidate
                    ).strip()
                    for check_name in [name_candidate, root_name]:
                        if check_name and len(check_name) > 3:
                            m_name = re.search(rf"\b{re.escape(check_name)}\b", full_text, flags=re.IGNORECASE)
                            if m_name:
                                matches.append({
                                    "ticker": ticker,
                                    "company_name": company_name,
                                    "match_type": "explicit_mention",
                                    "trigger_phrase": m_name.group(0),
                                    "database_field": "company_name",
                                    "details": f"Article explicitly mentions company name '{check_name}'."
                                })
                                explicit_found = True
                                break
                    if explicit_found:
                        break

        # Verified brands check (from match_keywords)
        if not explicit_found:
            for kw in cdata.get("match_keywords", []):
                kw_str = str(kw).strip()
                if not kw_str or kw_str.lower() in COMMON_GUARD_WORDS or len(kw_str) < 4:
                    continue
                # Brand match
                m_kw = re.search(rf"\b{re.escape(kw_str)}\b", full_text, flags=re.IGNORECASE)
                if m_kw:
                    matches.append({
                        "ticker": ticker,
                        "company_name": company_name,
                        "match_type": "explicit_mention",
                        "trigger_phrase": m_kw.group(0),
                        "database_field": "match_keywords",
                        "details": f"Article explicitly mentions verified brand / product / executive '{kw_str}' belonging to {company_name}."
                    })
                    explicit_found = True
                    break

        # -------------------------------------------------------------
        # 2. VERIFIED RELATIONSHIP
        # Named customer, supplier, or partner from research profile
        # -------------------------------------------------------------
        # Named Customers
        customers = cdata.get("named_customers")
        if isinstance(customers, list):
            for cust in customers:
                cname = cust.get("customer_name", "")
                clean_c = re.sub(r"(?i)\s+(?:limited|ltd\.?|india)$", "", cname).strip()
                if clean_c and len(clean_c) > 4:
                    m_cust = re.search(rf"\b{re.escape(clean_c)}\b", full_text, flags=re.IGNORECASE)
                    if m_cust:
                        rel = cust.get("relationship_type", "Key Customer")
                        source = cust.get("source_id", "research profile")
                        matches.append({
                            "ticker": ticker,
                            "company_name": company_name,
                            "match_type": "verified_relationship",
                            "trigger_phrase": m_cust.group(0),
                            "database_field": "named_customers",
                            "relationship": rel,
                            "entity_named": clean_c,
                            "source": f"research_profiles/{ticker.replace('.', '_')}.json ({source})",
                            "details": f"Article explicitly mentions verified customer '{clean_c}' ({rel} of {company_name}). Production/order fluctuations at the client directly transmit to component suppliers."
                        })
                        break

        # Named Partners / Suppliers
        partners = cdata.get("named_partners")
        if isinstance(partners, list):
            for part in partners:
                pname = part.get("entity_name", "")
                clean_p = re.sub(r"(?i)\s+(?:limited|ltd\.?|inc\.?|llc|\(france\)|\(japan\))$", "", pname).strip()
                if clean_p and len(clean_p) > 4 and clean_p.lower() not in ["google", "apple"]:
                    m_part = re.search(rf"\b{re.escape(clean_p)}\b", full_text, flags=re.IGNORECASE)
                    if m_part:
                        rel = part.get("relation_type", "Key Partner")
                        source = part.get("source_id", "research profile")
                        matches.append({
                            "ticker": ticker,
                            "company_name": company_name,
                            "match_type": "verified_relationship",
                            "trigger_phrase": m_part.group(0),
                            "database_field": "named_partners",
                            "relationship": rel,
                            "entity_named": clean_p,
                            "source": f"research_profiles/{ticker.replace('.', '_')}.json ({source})",
                            "details": f"Article explicitly mentions verified partner '{clean_p}' ({rel} of {company_name})."
                        })
                        break

        # -------------------------------------------------------------
        # 3. SECTOR HYPOTHESIS (Marked as hypothesis, never confirmed)
        # -------------------------------------------------------------
        # Case A: Domestic Indian Monetary Policy (RBI Repo Rate)
        if geo_info["relevant_institution"] == "Reserve Bank of India" and geo_info["india_connection_established"]:
            if ticker in ["DCBBANK.BO", "REPCOHOME.BO"]:
                m_rbi = re.search(r"\b(rbi|repo rate|monetary policy committee)\b", title_lower)
                trigger = m_rbi.group(0) if m_rbi else "RBI monetary policy"
                ind_name = cdata.get("industry", "Financials")
                matches.append({
                    "ticker": ticker,
                    "company_name": company_name,
                    "match_type": "sector_hypothesis",
                    "trigger_phrase": trigger,
                    "database_field": "industry",
                    "hypothesis": f"Domestic RBI repo rate adjustments influence commercial lending and deposit rates for {ind_name} operators.",
                    "missing_connection": "Net interest margin (NIM) impact depends on internal asset-liability maturity mismatches and speed of floating-rate loan repricing.",
                    "details": f"Hypothesis only: RBI interest rate decisions systematically transmit to Indian domestic lending institutions like {company_name}."
                })

        # Case B: US Fed Monetary Policy (Strictly unverified hypothesis for Indian banks)
        elif geo_info["relevant_institution"] == "US Federal Reserve" and not geo_info["india_connection_established"]:
            # Do NOT attach automatically as verified; if noted, must explain the missing domestic connection
            pass  # Do not attach to US Fed events to fulfill Requirement 5

        # Case C: Online Gaming / Goa Tourism Regulation for Delta Corp
        if ticker == "DELTACORP.BO":
            m_tax = re.search(r"\b(28% gst on online gaming|online gaming gst|casino tax|goa casino)\b", full_text_lower)
            if m_tax and not explicit_found:
                matches.append({
                    "ticker": ticker,
                    "company_name": company_name,
                    "match_type": "sector_hypothesis",
                    "trigger_phrase": m_tax.group(0),
                    "database_field": "key_dependencies",
                    "hypothesis": "Changes to Goa casino regulations or online gaming GST frameworks directly impact the casino and skill-gaming operational model.",
                    "missing_connection": "Specific applicability to Delta Corp's offshore vessels or Adda52 platform depends on formal state tax assessment notices.",
                    "details": "Hypothesis only: Regulatory / taxation headwind affects gaming sector operators including Delta Corp."
                })

    return matches


# ==============================================================================
# 4. OFFLINE TESTS (Strict Verification Suite)
# ==============================================================================
def run_offline_tests(db: dict) -> bool:
    """
    Executes small offline unit tests with fixed sample headlines to ensure:
      1. US Fed vs RBI are strictly separated by geography and institution.
      2. Anthropic IPO vs Bajaj Finance warrants are strictly separated.
      3. Direct Delta Corp mention matches DELTACORP.BO with 'explicit_mention'.
      4. A US Fed headline alone does NOT produce a verified DCB Bank or Repco match.
    """
    print("=" * 60)
    print("RUNNING OFFLINE VALIDATION TESTS")
    print("=" * 60)
    failures = []

    # Test 1: US Fed vs RBI separation
    h_fed = "Traders raise odds of 50 bps Fed rate hike as CPI rise exceeds expectations"
    h_rbi = "RBI raises repo rate by 25 bps to tame domestic food inflation"

    geo_fed = classify_geography_and_institution(h_fed)
    geo_rbi = classify_geography_and_institution(h_rbi)
    grp_fed = determine_story_group(h_fed)
    grp_rbi = determine_story_group(h_rbi)

    if grp_fed == grp_rbi:
        failures.append(f"Test 1 Failed: US Fed and RBI grouped into same group '{grp_fed}'")
    if geo_fed["relevant_institution"] != "US Federal Reserve":
        failures.append(f"Test 1 Failed: Fed headline institution is '{geo_fed['relevant_institution']}' (expected US Federal Reserve)")
    if geo_rbi["relevant_institution"] != "Reserve Bank of India":
        failures.append(f"Test 1 Failed: RBI headline institution is '{geo_rbi['relevant_institution']}' (expected Reserve Bank of India)")
    if geo_fed["geography"] != "US":
        failures.append(f"Test 1 Failed: Fed headline geography is '{geo_fed['geography']}' (expected US)")
    if geo_rbi["geography"] != "India":
        failures.append(f"Test 1 Failed: RBI headline geography is '{geo_rbi['geography']}' (expected India)")

    if not failures:
        print("  [PASS] Test 1: US Fed vs RBI institution, geography, and groups strictly separated.")

    # Test 2: Anthropic IPO vs Bajaj Finance warrants separation
    h_anthropic = "Anthropic's potential mega IPO joins ranks of world's biggest stock market listings"
    h_bajaj = "Bajaj Finance issues 5.74 crore warrants to promoter for Rs 5,799 crore"

    grp_anthropic = determine_story_group(h_anthropic)
    grp_bajaj = determine_story_group(h_bajaj)

    if grp_anthropic == grp_bajaj:
        failures.append(f"Test 2 Failed: Anthropic IPO and Bajaj Finance warrants grouped together into '{grp_anthropic}'")
    else:
        print(f"  [PASS] Test 2: Anthropic ('{grp_anthropic}') and Bajaj Finance ('{grp_bajaj}') strictly separated.")

    # Test 3: Direct Delta Corp mention matches DELTACORP.BO as 'explicit_mention'
    h_delta = "Delta Corp secures court interim stay on high GST tax demand in Goa"
    geo_delta = classify_geography_and_institution(h_delta)
    matches_delta = match_article_to_companies(h_delta, "", "", db, geo_delta)

    delta_match = next((m for m in matches_delta if m["ticker"] == "DELTACORP.BO"), None)
    if not delta_match:
        failures.append("Test 3 Failed: DELTACORP.BO not matched on headline containing 'Delta Corp'")
    elif delta_match["match_type"] != "explicit_mention":
        failures.append(f"Test 3 Failed: Match type is '{delta_match['match_type']}' (expected explicit_mention)")
    elif delta_match["trigger_phrase"] != "Delta Corp":
        failures.append(f"Test 3 Failed: Trigger phrase is '{delta_match['trigger_phrase']}' (expected Delta Corp)")
    else:
        print(f"  [PASS] Test 3: Direct Delta Corp mention matched DELTACORP.BO with match_type='explicit_mention', trigger='{delta_match['trigger_phrase']}'.")

    # Test 4: US Fed headline alone must NOT produce a verified DCB Bank or Repco match
    h_us_fed_alone = "US Treasury yields rise as Fed's Waller presses rate hike case"
    geo_us_fed = classify_geography_and_institution(h_us_fed_alone)
    matches_fed = match_article_to_companies(h_us_fed_alone, "", "", db, geo_us_fed)

    verified_fed_matches = [m for m in matches_fed if m["match_type"] in ["explicit_mention", "verified_relationship"]]
    dcb_repco_verified = [m for m in verified_fed_matches if m["ticker"] in ["DCBBANK.BO", "REPCOHOME.BO"]]

    if dcb_repco_verified:
        failures.append(f"Test 4 Failed: US Fed headline produced verified match for Indian lenders: {dcb_repco_verified}")
    else:
        print("  [PASS] Test 4: US Fed headline alone produced 0 verified matches for DCB Bank and Repco Home Finance.")

    print("=" * 60)
    if failures:
        print("OFFLINE TESTS FAILED:")
        for f in failures:
            print("  ✖", f)
        return False
    else:
        print("ALL 4 OFFLINE TESTS PASSED SUCCESSFULLY!")
        print("=" * 60 + "\n")
        return True


# ==============================================================================
# 5. PIPELINE EXECUTION
# ==============================================================================
def run_pipeline():
    db = load_database()

    # Always execute offline tests first
    tests_ok = run_offline_tests(db)
    if not tests_ok:
        print("Aborting pipeline due to test failures.")
        sys.exit(1)

    print("=" * 60)
    print("LIVE RSS PROCESSING & COMPANY MATCHING")
    print("=" * 60)
    print(f"Fetching {MAX_HEADLINES} RSS headlines from {RSS_URL}...")

    try:
        resp = httpx.get(RSS_URL, headers=REQ_HEADERS, follow_redirects=True, timeout=15)
        root = ET.fromstring(resp.text)
        raw_items = root.findall(".//item")[:MAX_HEADLINES]
    except Exception as e:
        print(f"ERROR: Failed to fetch RSS feed: {e}")
        sys.exit(1)

    items = []
    print(f"Retrieved {len(raw_items)} items. Extracting details & content...")

    for idx, item in enumerate(raw_items, 1):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        description = (item.findtext("description") or "").strip()
        score = score_headline(title)

        # Download full article text for deep body search
        article_content = ""
        if link:
            try:
                art_resp = httpx.get(link, headers=REQ_HEADERS, follow_redirects=True, timeout=8)
                article_content = trafilatura.extract(art_resp.text) or ""
            except Exception:
                pass

        geo_info = classify_geography_and_institution(title, description)

        items.append({
            "id": idx,
            "title": title,
            "link": link,
            "pub_date": pub_date,
            "description": description,
            "article_content": article_content,
            "score": score,
            "geo_info": geo_info
        })

    # Group stories strictly by underlying event or entity
    groups, singles = build_event_groups(items)

    # Match each item against database
    output_events = []

    for item in items:
        matches = match_article_to_companies(
            item["title"],
            item["description"],
            item["article_content"],
            db,
            item["geo_info"]
        )

        verified_tickers = sorted(list({m["ticker"] for m in matches if m["match_type"] in ["explicit_mention", "verified_relationship"]}))
        hypothesis_tickers = sorted(list({m["ticker"] for m in matches if m["match_type"] == "sector_hypothesis"}))

        output_events.append({
            "event": {
                "title": item["title"],
                "link": item["link"],
                "pub_date": item["pub_date"],
                "description": item["description"],
                "geography": item["geo_info"]["geography"],
                "relevant_institution": item["geo_info"]["relevant_institution"],
                "india_connection_established": item["geo_info"]["india_connection_established"]
            },
            "score": item["score"],
            "group": item.get("group", "General"),
            "tickers": verified_tickers,
            "hypothesis_tickers": hypothesis_tickers,
            "why_matched": matches
        })

    # Save to matched_news.json
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        json.dump(output_events, out, indent=2, ensure_ascii=False)

    print(f"Processed {len(output_events)} articles. Saved results to {OUTPUT_FILE}.\n")

    # Display Story Groups
    print("=" * 60)
    print("CORRECTED STORY GROUPS & SCORES:")
    print("=" * 60)
    if groups:
        for idx, g in enumerate(groups, 1):
            print(f"\nGROUP {idx}: {g['label']} (Combined Score: {g['score']})")
            for it in g["items"]:
                print(f"  • [{it['score']} pts] [{it['geo_info']['geography']}] {it['title']}")

    if singles:
        print("\nSTANDALONE ITEMS:")
        for s in singles:
            print(f"  • [{s['score']} pts] [{s['geo_info']['geography']}] {s['title']}")

    # --------------------------------------------------------------------------
    # TOP 3 RESULTS FOR CHECKING
    # --------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("TOP 3 RESULTS FOR CHECKING (Ranked by Verified Matches & Score):")
    print("=" * 60)

    # Rank by: verified match presence first, then score descending
    sorted_events = sorted(
        output_events,
        key=lambda x: (len(x["tickers"]) > 0, x["score"]),
        reverse=True
    )

    top_3 = sorted_events[:3]

    for i, ev in enumerate(top_3, 1):
        print(f"\n--- MATCH #{i} ---")
        print(f"Title         : {ev['event']['title']}")
        print(f"Score         : {ev['score']}")
        print(f"Group         : {ev['group']}")
        print(f"Geography     : {ev['event']['geography']} (Institution: {ev['event']['relevant_institution']})")
        print(f"India Context : {'Established' if ev['event']['india_connection_established'] else 'None (Foreign Macro)'}")
        print(f"Verified Tickers : {ev['tickers'] if ev['tickers'] else 'None'}")
        if ev["hypothesis_tickers"]:
            print(f"Hypothesis Tickers: {ev['hypothesis_tickers']}")

        print("Why Matched:")
        if ev["why_matched"]:
            for wm in ev["why_matched"]:
                print(f"  • [{wm['ticker']} - {wm['company_name']}]")
                print(f"    - Type         : {wm['match_type']}")
                print(f"    - Trigger Phrase: \"{wm['trigger_phrase']}\"")
                print(f"    - Database Field: {wm['database_field']}")
                if "relationship" in wm:
                    print(f"    - Relationship : {wm['relationship']} (Source: {wm.get('source', '')})")
                if "missing_connection" in wm:
                    print(f"    - Missing Link : {wm['missing_connection']}")
                print(f"    - Details      : {wm['details']}")
        else:
            print("  • No explicit mention, verified relationship, or domestic sector hypothesis found.")

    return output_events


def main():
    parser = argparse.ArgumentParser(description="Match news against company database")
    parser.add_argument("--test", action="store_true", help="Run offline validation test suite only")
    args = parser.parse_args()

    db = load_database()
    if args.test:
        ok = run_offline_tests(db)
        sys.exit(0 if ok else 1)

    run_pipeline()


if __name__ == "__main__":
    main()
