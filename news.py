"""
Interactive RSS Market News Headline Browser & Analyzer
Headlines are scored by importance and grouped by topic/company before showing.
"""

import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

# Add screener-scraper to path
sys.path.append(os.path.abspath("screener-scraper"))

import httpx
import ollama
from screenerScraper import ScreenerScrape
import trafilatura

# ==============================================================================
# INPUTS
# ==============================================================================
RSS_URL = "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"
MODEL_NAME = "qwen3:4b"  # Local Ollama model
MAX_HEADLINES = 15        # Fetch more so grouping is meaningful

# ==============================================================================
# SCORING & GROUPING LOGIC
# ==============================================================================

HIGH_KEYWORDS   = ["rbi", "fed", "crash", "plunge", "soar", "surge", "merger",
                   "acquisition", "acquire", "profit", "loss", "default",
                   "bankrupt", "inflation", "rate hike", "rate cut", "repo"]
MEDIUM_KEYWORDS = ["earnings", "revenue", "growth", "decline", "upgrade",
                   "downgrade", "ipo", "dividend", "quarterly", "results", "q1",
                   "q2", "q3", "q4"]
LOW_KEYWORDS    = ["52-week", "technical", "chart", "live update", "nifty",
                   "sensex", "trade setup"]

# Topic cluster seeds: (label, list of related keywords)
TOPIC_CLUSTERS = [
    ("RBI / Rate",    ["rbi", "repo", "rate hike", "rate cut", "monetary policy"]),
    ("Fed / US",      ["fed", "federal reserve", "us market", "us economy", "dollar"]),
    ("Crude / Oil",   ["crude", "oil", "opec", "brent", "petroleum"]),
    ("Gold / Metal",  ["gold", "silver", "metal", "commodity"]),
    ("Inflation",     ["inflation", "cpi", "wpi", "price rise"]),
    ("IPO",           ["ipo", "listing", "initial public"]),
    ("Earnings",      ["profit", "loss", "earnings", "quarterly", "results", "q1",
                       "q2", "q3", "q4", "revenue"]),
    ("Market Move",   ["crash", "plunge", "soar", "surge", "rally", "fall",
                       "nifty", "sensex", "52-week"]),
]


def score_headline(title: str) -> int:
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
    return max(score, 1)                       # floor at 1


def extract_company(title: str) -> str | None:
    """
    Heuristic: returns the first CamelCase/all-caps token that looks like a
    company name or NSE/BSE ticker (e.g. 'TCS', 'UnoMinda', 'Infosys').
    Returns None when nothing credible is found.
    """
    # Remove common noise words
    noise = {"the", "of", "in", "on", "at", "to", "and", "a", "an",
             "for", "is", "are", "was", "were", "this", "that"}
    words = re.findall(r"[A-Za-z][A-Za-z0-9]*", title)
    for word in words:
        if word.lower() in noise:
            continue
        # All-caps tickers (2-7 letters)
        if re.fullmatch(r"[A-Z]{2,7}", word):
            return word
        # CamelCase / starts-uppercase words longer than 4 chars
        if word[0].isupper() and len(word) > 4 and not word.isupper():
            return word.lower()          # normalise for grouping
    return None


def topic_cluster(title: str) -> str | None:
    t = title.lower()
    for label, keywords in TOPIC_CLUSTERS:
        if any(kw in t for kw in keywords):
            return label
    return None


def build_groups(items):
    """
    items: list of dicts with keys: title, link, score
    Returns:
        groups     – list of {"label": str, "score": int, "items": [item, ...]}
        singles    – list of items that didn't fit a group
    """
    # 1) Try to place each headline into a topic cluster
    cluster_map = defaultdict(list)   # label -> [item]
    for item in items:
        label = topic_cluster(item["title"])
        if label:
            cluster_map[label].append(item)
        else:
            # 2) Fallback: group by extracted company name
            company = extract_company(item["title"])
            if company:
                cluster_map[f"Company: {company.title()}"].append(item)
            else:
                cluster_map["__single__"].append(item)

    groups = []
    singles = []
    for label, group_items in cluster_map.items():
        if label == "__single__":
            singles.extend(group_items)
        elif len(group_items) == 1:
            singles.extend(group_items)       # solo items → singles
        else:
            total_score = sum(i["score"] for i in group_items)
            # Sort members inside group by score descending
            group_items.sort(key=lambda x: x["score"], reverse=True)
            groups.append({"label": label, "score": total_score, "items": group_items})

    # Sort groups and singles by score descending
    groups.sort(key=lambda g: g["score"], reverse=True)
    singles.sort(key=lambda i: i["score"], reverse=True)
    return groups, singles


def display_groups(groups, singles):
    """Prints the grouped + single views. Returns a flat selection map."""
    # selection_map: letter/number typed by user -> item dict
    selection_map = {}
    group_num = 1

    if groups:
        for g in groups:
            print(f"\nGROUP {group_num}: {g['label']}  (Score: {g['score']})")
            for idx, item in enumerate(g["items"], start=1):
                key = f"{group_num}.{idx}"
                selection_map[key] = item
                print(f"    {key}  {item['title']}  [{item['score']}]")
            # Allow selecting the whole group (pick the top-scored item)
            selection_map[str(group_num)] = g["items"][0]
            group_num += 1

    if singles:
        print("\nSINGLE ITEMS:")
        for item in singles:
            key = str(group_num)
            selection_map[key] = item
            print(f"  {key:>4}. {item['title']}  [Score: {item['score']}]")
            group_num += 1

    return selection_map


# ==============================================================================
# 1. FETCH & PARSE RSS FEED
# ==============================================================================
req_headers = {"User-Agent": "Mozilla/5.0"}
print("Fetching latest market headlines...\n")
xml_text = httpx.get(RSS_URL, headers=req_headers, follow_redirects=True, timeout=20).text
rss_root = ET.fromstring(xml_text)
rss_items = rss_root.findall(".//item")[:MAX_HEADLINES]

headlines = []
for item in rss_items:
    title = (item.findtext("title") or "").strip()
    link  = (item.findtext("link") or "").strip()
    if title and link:
        headlines.append({"title": title, "link": link, "score": score_headline(title)})

# ==============================================================================
# 2. SCORE, GROUP & DISPLAY
# ==============================================================================
groups, singles = build_groups(headlines)
selection_map = display_groups(groups, singles)

# ==============================================================================
# 3. USER PICKS AN ARTICLE
# ==============================================================================
print()
choice = input("Pick a number (e.g. 1 for whole group, 1.2 for specific article): ").strip()

if choice not in selection_map:
    raise SystemExit(f"Invalid choice '{choice}'. Exiting.")

selected = selection_map[choice]
URL = selected["link"]
print(f"\nSelected: {selected['title']}")
print(f"URL: {URL}")

SCREENER_SYMBOL = input(
    "Type the Screener symbol (e.g. RBLBANK, or press Enter to skip): "
).strip().upper()

# ==============================================================================
# 4. DOWNLOAD & EXTRACT ARTICLE TEXT
# ==============================================================================
html = httpx.get(URL, headers=req_headers, follow_redirects=True, timeout=20).text
text = trafilatura.extract(html)
if not text:
    raise SystemExit("Could not extract text. Try another article.")
text = text[:10000]
print("\nExtracted characters:", len(text))

# ==============================================================================
# 5. FETCH SCREENER.IN FINANCIAL DATA (IF SYMBOL GIVEN)
# ==============================================================================
screener_data = "No Screener data provided."
if SCREENER_SYMBOL:
    print(f"Fetching Screener data for {SCREENER_SYMBOL}...")
    scraper = ScreenerScrape()
    scraper.loadScraper(SCREENER_SYMBOL, consolidated=True)
    screener_data = {
        "P&L": scraper.pnlReport(withAddon=True),
        "Balance Sheet": scraper.balanceSheet(withAddon=True),
        "Ratios": scraper.ratios(),
    }
    print("Screener data retrieved successfully.")

# ==============================================================================
# 6. LLM ANALYSIS (2-STEP OLLAMA PROMPT)
# ==============================================================================
options = {"temperature": 0, "num_ctx": 12288, "seed": 42}

# --- STEP 1: Extract facts ---
prompt_a = f"""STRICT RULES: Use ONLY the article below. Do NOT invent anything.
Extract a list of every company, every number, and every date.
For each item, write exactly what it refers to.
Always write the amount together with its label.
Format as a simple list, one fact per line.

ARTICLE:
{text}
"""

print("\nRunning Step 1: Extracting facts...")
reply_a = ollama.chat(
    model=MODEL_NAME,
    messages=[{"role": "user", "content": prompt_a}],
    options=options,
    think=True,
)

print("\n" + "=" * 60)
print("--- STEP 1 OUTPUT: EXTRACTED LIST ---")
print("=" * 60)
print(reply_a.message.content)

# --- STEP 2: Final Report ---
prompt_b = f"""STRICT RULES: Do NOT invent companies, dates, or numbers.
Do NOT do your own math. If something is missing, write "Not stated."
Use the EXTRACTED LIST to keep facts separate. Use the ARTICLE for quotes.
Use the SCREENER DATA to check the article's claims against the company's real history.
If the article describes more than one deal, treat them separately.
In section 6, give one exact quote from the ARTICLE for each main claim.
In section 7, first state the thesis in one sentence, then what would prove it wrong.

EXTRACTED LIST:
{reply_a.message.content}

ARTICLE:
{text}

SCREENER DATA (Real financial history of the company):
{screener_data}

TASK: Write ONLY these 7 sections:
1. Event understanding
2. Transmission of effects
3. Company discovery
4. Company-level consequences
5. Financial significance (use Screener data here to check historical context)
6. Evidence assessment (quote the article)
7. Investment assessment (thesis, then what would invalidate it)
"""

print("\nRunning Step 2: Generating final report...")
reply_b = ollama.chat(
    model=MODEL_NAME,
    messages=[{"role": "user", "content": prompt_b}],
    options=options,
    think=True,
)

print("\n" + "=" * 60)
print("--- STEP 2 OUTPUT: FINAL REPORT ---")
print("=" * 60)
print(reply_b.message.content)
