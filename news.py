import sys
import os

# Add the scraper folder so Python can find the module
sys.path.append(os.path.abspath("screener-scraper"))

import httpx
import trafilatura
import ollama
import xml.etree.ElementTree as ET
from screenerScraper import ScreenerScrape

RSS_URL = "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"

# --- PART 1: Show the latest headlines ---
headers = {"User-Agent": "Mozilla/5.0"}
xml_text = httpx.get(RSS_URL, headers=headers, follow_redirects=True, timeout=20).text
root = ET.fromstring(xml_text)
items = root.findall(".//item")

print("LATEST HEADLINES:\n")
for i, item in enumerate(items[:10], start=1):
    print(f"{i}. {item.findtext('title')}")

# --- PART 2: You pick one ---
choice = int(input("\nPick a number: "))
URL = items[choice - 1].findtext("link")
SCREENER_SYMBOL = input("Type the Screener symbol (e.g. RBLBANK, or press Enter to skip): ").strip().upper()

# --- PART 3: Fetch the article ---
html = httpx.get(URL, headers=headers, follow_redirects=True, timeout=20).text
text = trafilatura.extract(html)
if not text:
    raise SystemExit("Could not extract text. Try another article.")
text = text[:10000]
print("\nExtracted characters:", len(text))

# --- PART 4: Get Screener data (only if you gave a symbol) ---
screener_data = "No Screener data provided."
if SCREENER_SYMBOL:
    os.chdir("screener-scraper")  # so the scraper finds its tokens folder
    print(f"Fetching Screener data for {SCREENER_SYMBOL}...")
    scraper = ScreenerScrape()
    token = scraper.getBSEToken(SCREENER_SYMBOL)
    scraper.loadScraper(token, consolidated=True)
    screener_data = {
        "P&L": scraper.pnlReport(withAddon=True),
        "Balance Sheet": scraper.balanceSheet(withAddon=True),
        "Ratios": scraper.ratios(),
    }

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

print("\nRunning Step 1 (Extracting facts)...")
reply_a = ollama.chat(
    model="qwen3:4b",
    messages=[{"role": "user", "content": prompt_a}],
    options=options,
    think=True,
)

print("\n--- STEP 1 OUTPUT: EXTRACTED LIST ---")
print(reply_a.message.content)

# --- STEP 2: Final report ---
prompt_b = f"""STRICT RULES: Do NOT invent companies, dates, or numbers.
Do NOT do your own math. If something is missing, write "Not stated."
Use the EXTRACTED LIST to keep facts separate. Use the ARTICLE for quotes.
Use the SCREENER DATA only if the article is about that company. If it is not relevant, say so in section 5.
In section 6, give one exact quote from the ARTICLE for each main claim.
In section 7, first state the thesis in one sentence, then what would prove it wrong.

EXTRACTED LIST:
{reply_a.message.content}

ARTICLE:
{text}

SCREENER DATA:
{screener_data}

TASK: Write ONLY these 7 sections:
1. Event understanding
2. Transmission of effects
3. Company discovery
4. Company-level consequences
5. Financial significance
6. Evidence assessment (quote the article)
7. Investment assessment (thesis, then what would invalidate it)
"""

print("\n--- STEP 2 OUTPUT: FINAL REPORT ---")
reply_b = ollama.chat(
    model="qwen3:4b",
    messages=[{"role": "user", "content": prompt_b}],
    options=options,
    think=True,
)

print(reply_b.message.content)