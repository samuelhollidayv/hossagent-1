"""
Web tools for the research agent: web_search and web_fetch.
"""

import re
import urllib.parse
import requests
from bs4 import BeautifulSoup


def web_search(query: str) -> list[dict]:
    """
    Search the web using DuckDuckGo HTML results.
    Returns a list of dicts with title and url.
    """
    try:
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        results = []
        for result in soup.select(".result"):
            if "result--ad" in result.get("class", []):
                continue
            
            title_elem = result.select_one(".result__title")
            anchor = result.select_one("a.result__a")
            
            if title_elem and anchor:
                title = title_elem.get_text(strip=True)
                href = str(anchor.get("href", ""))
                
                if href.startswith("//duckduckgo.com/l/?uddg="):
                    parsed = urllib.parse.urlparse(href)
                    params = urllib.parse.parse_qs(parsed.query)
                    if "uddg" in params:
                        href = urllib.parse.unquote(params["uddg"][0])
                elif href.startswith("/"):
                    href = "https://duckduckgo.com" + href
                
                if title and href.startswith("http") and "duckduckgo.com/y.js" not in href:
                    results.append({
                        "title": title,
                        "url": href
                    })
                
                if len(results) >= 5:
                    break
        
        if not results:
            return [{
                "title": f"No results found for: {query}",
                "url": ""
            }]
        
        return results
        
    except Exception as e:
        return [{
            "title": f"Search error: {str(e)}",
            "url": ""
        }]


def web_fetch(url: str) -> str:
    """
    Fetch a web page and return its text content.
    Uses BeautifulSoup for graceful HTML parsing.
    """
    if not url or not url.startswith("http"):
        return "Error: Invalid URL provided"
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        for element in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "iframe"]):
            element.decompose()
        
        main_content = soup.find("main") or soup.find("article") or soup.find("body")
        
        if main_content:
            text = main_content.get_text(separator=" ", strip=True)
        else:
            text = soup.get_text(separator=" ", strip=True)
        
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = " ".join(lines)
        
        text = re.sub(r'\s+', ' ', text)
        
        return text[:5000] if text else "No readable content found on page"
        
    except requests.exceptions.Timeout:
        return f"Error: Request timed out for {url}"
    except requests.exceptions.HTTPError as e:
        return f"Error: HTTP {e.response.status_code} for {url}"
    except Exception as e:
        return f"Error fetching {url}: {str(e)}"
