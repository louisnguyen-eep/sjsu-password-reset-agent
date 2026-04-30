"""Scrape SJSU IT documentation pages into a JSON file.

Run: python3.14 -m knowledge.scrape

Output: knowledge/scraped_docs.json — used by ingest.py to build the vector store.

The scraper is intentionally simple: fetches each URL, strips nav/footer
boilerplate, and saves the main content. If a page layout changes and
parsing breaks, you can manually edit scraped_docs.json before ingesting.
"""
import json
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# URLs to scrape — these are the SJSU pages most relevant to password reset.
# Add more as you find them. Each entry becomes one or more chunks in RAG.
URLS = [
    # Core password help — confirmed working
    "https://www.sjsu.edu/it/services/support/password-help.php",

    # First-time login & MFA setup — important for student onboarding questions
    "https://www.sjsu.edu/it/services/support/it-service-desk/first-time-login.php",
    "https://www.sjsu.edu/it/support/first-time-user.php",

    # Account reactivation / alumni rules
    "https://blogs.sjsu.edu/mysjsu/tag/password/",

    # External knowledge base entry on SJSUOne reset (Moss Landing campus)
    "https://kb.mlml.sjsu.edu/books/network-services/page/reset-your-sjsuone-password",

    # iSupport FAQ — has the most detailed Q&A on password edge cases
    "https://isupport.sjsu.edu/AS/Faq?itemGuid=fe4aed66-03b0-48cd-9a3b-60d1f019b828",
]


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}


def scrape_page(url: str) -> dict | None:
    """Fetch a URL and extract clean text from the main content area."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"  ✗ Failed to fetch {url}: {e}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    # Remove noise — nav, footer, scripts, styles
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # Try to find the main content area. SJSU uses several patterns —
    # we try in order and fall back to <body>.
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find("div", {"id": "main-content"})
        or soup.find("div", {"class": "main-content"})
        or soup.body
    )

    if main is None:
        print(f"  ✗ No main content found in {url}")
        return None

    # Extract title
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else url

    # Extract text — preserve paragraph breaks but collapse whitespace
    text_chunks = []
    for elem in main.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        text = elem.get_text(" ", strip=True)
        if text and len(text) > 20:  # skip empty / tiny fragments
            text_chunks.append(text)

    content = "\n\n".join(text_chunks)

    if len(content) < 100:
        print(f"  ✗ {url} returned too little content ({len(content)} chars)")
        return None

    print(f"  ✓ {url} — {len(content)} chars")
    return {
        "id": url.split("/")[-1].replace(".php", "").replace(".html", ""),
        "source": url,
        "title": title,
        "content": content,
    }


def main():
    print(f"Scraping {len(URLS)} SJSU IT pages...")
    docs = []
    for url in URLS:
        result = scrape_page(url)
        if result:
            docs.append(result)
        time.sleep(1)  # be polite — 1 sec between requests

    output_path = Path(__file__).parent / "scraped_docs.json"
    with output_path.open("w") as f:
        json.dump(docs, f, indent=2)

    print(f"\nWrote {len(docs)} documents to {output_path}")
    total_chars = sum(len(d["content"]) for d in docs)
    print(f"Total content: {total_chars:,} characters")


if __name__ == "__main__":
    main()