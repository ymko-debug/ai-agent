
from typing import Optional


def scrape_url_with_playwright(url: str) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=20000)
            page.wait_for_load_state("networkidle", timeout=15000)
            text = page.evaluate(
                """() => {
const els = document.querySelectorAll(
  'table, tr, td, th, li, p, h1, h2, h3, h4, [class*="result"], [class*="row"], [class*="item"]'
);
return Array.from(els)
  .map(el => el.innerText?.trim())
  .filter(t => t && t.length > 2)
  .join('\n');
}"""
            )
            browser.close()
            return text[:8000] if text else None
    except Exception:
        return None
