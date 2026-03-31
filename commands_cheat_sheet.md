# Agent Commands & Actions Guide

This file contains the specific triggers and commands needed to interact with the AI assistant's special capabilities.

## 1. Lead Generation Pipeline

**Command:** `/leads niche=[industry] location=[city]`
**Example:** `/leads niche=plumbers location=seattle`

**What it does:**
1. Triggers the specialized "Command Router" instead of a normal chat.
2. Web searches for the top businesses matching the niche and location.
3. Uses the "Strong Lane" (Claude) to extract structured data (Names, Emails, Phones).
4. Automatically saves the extracted data as an Excel (`.xlsx`) file inside the `output/` directory.

## 2. Browser Automation

**How to trigger:** Use explicit keywords in your chat prompt that indicate browser intent.
**Keywords to use:** `navigate`, `go to`, `open`, `view`, `click on`, `type ... into`, `close browser`
**Examples:**
- "open https://google.com"
- "navigate to wikipedia and search for AI"
- "click on the 'About Us' link"
- "close the browser"

**What it does:**
1. Detects "Browser Intent" before sending the message to the LLM.
2. Uses Playwright Chromium to launch a real, headless browser.
3. Executes the action (clicking, typing, reading the page).
4. Returns the visible text from the webpage into the LLM's context so it can answer questions about the page content.

## 3. Public Business Registry Scraping

**How to trigger:** Ask the agent to find business records or companies.
**Keywords to use:** `business`, `company`, `owner`, `registry`, `incorporated`, `find`
**Example:** "Find the official business registration for Olympia Plumbing Pros in WA"

**What it does:**
1. Detects "Business Lookup" intent.
2. Searches the web specifically for official government registry URLs (e.g. `.gov`, `sos.`, `corporations`).
3. Uses the Playwright scraper to bypass Javascript-heavy pages and extract the visible registry table.
4. Feeds the legal registry data (Owner names, Registered Agents, etc) directly into the LLM's context window.

## 4. Web Search Toggle

**How to trigger:** The "Web Search" toggle in the Streamlit Sidebar.
**What it does:**
- **ON:** Every chat message runs through a heuristic (`needs_search`). If the agent thinks it needs current information (e.g. "latest news", "price of"), it will query Tavily and insert up-to-date web results into its context before answering.
- **OFF:** The agent relies purely on its training data.

## 5. Provider Overrides

**How to trigger:** The "Provider Override" dropdown in the Streamlit Sidebar.
**What it does:**
By default, the casual chat window uses the "General Lane" which prioritizes: Claude -> OpenRouter -> NVIDIA. If you want to force the chat to use a cheap model or skip Claude entirely, select a different provider from this dropdown.
