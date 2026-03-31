
from typing import Optional


def scrape_url_with_playwright(url: str) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars"
                ]
            )
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            # Hide webdriver property
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
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
