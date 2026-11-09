# Stock Impact Analysis

An automated pipeline that correlates financial news with fundamental company data from [Screener.in](https://www.screener.in) to perform an LLM-assisted impact assessment.

## Project Structure

```text
stock-project/
├── analyze.py              # Analyzes a specific article URL for a company
├── news.py                 # Interactive terminal RSS news headline picker & analyzer
├── screener-scraper/       # Clean Screener.in financial statement scraper
│   ├── screenerScraper.py  # Scraper module
│   └── testScreener.py     # Simple test script
├── requirements.txt        # Project dependencies
└── README.md               # Documentation
```

## Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Ensure Ollama is running**:
   ```bash
   ollama pull qwen3:4b
   ```

## Usage

### 1. Analyze a specific article URL:
Edit the `URL` and `SCREENER_SYMBOL` at the top of `analyze.py`, then run:
```bash
python analyze.py
```

### 2. Browse live market headlines interactively:
Run `news.py` to see top 10 market headlines from ET RSS, pick one by number, and analyze it:
```bash
python news.py
```

### 3. Test the Screener scraper:
```bash
python screener-scraper/testScreener.py
```
