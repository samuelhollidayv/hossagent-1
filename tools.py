"""
Web tools for the research agent: web_search and web_fetch.
"""

import re
import requests


def web_search(query: str) -> list[dict]:
    """
    Search the web for a query. Returns a list of dicts with title and url.
    This is a stub implementation with mock results for MVP.
    """
    mock_results = [
        {
            "title": f"Result 1 for: {query}",
            "url": "https://example.com/article1",
        },
        {
            "title": f"Result 2 for: {query}",
            "url": "https://example.com/article2",
        },
        {
            "title": f"Result 3 for: {query}",
            "url": "https://example.com/article3",
        },
    ]
    return mock_results


def web_fetch(url: str) -> str:
    """
    Fetch a web page and return its text content.
    Uses simple HTML tag stripping.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; HossAgent/1.0)"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        html = response.text
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:5000]
    except Exception as e:
        return f"Error fetching {url}: {str(e)}"
