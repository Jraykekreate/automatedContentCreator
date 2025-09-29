#!/usr/bin/env python3
import random
import time
import re
import json
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

# ---- defaults (can be overridden by function args) ----
CHROMEDRIVER_PATH = "./chromedriver"
DEFAULT_SEARCH_QUERY = "chelsea vs benfica"
DEFAULT_SAVE_JSON = "balldata_2.json"
# ------------------------------------------------------


def human_type(element, text, min_delay=0.05, max_delay=0.18):
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(min_delay, max_delay))


def extract_match_id(text: str):
    """Try multiple patterns to find a FotMob match id number."""
    if not text:
        return None
    patterns = [
        r"#(\d+)",                    # fragment like #4813427
        r"matchId=(\d+)",             # query param matchId=4813427
        r"/(\d{5,8})(?:$|[#/?:\-])"   # numeric path segment (conservative length)
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return None


def clean_headers(raw_headers: dict):
    """Remove pseudo-headers (':method', ':authority', etc.) and ensure strings."""
    if not raw_headers:
        return {}
    cleaned = {}
    for k, v in raw_headers.items():
        if isinstance(k, str) and k.startswith(":"):
            continue
        cleaned[str(k)] = str(v)
    return cleaned


def scrape_match(
    search_query: str = DEFAULT_SEARCH_QUERY,
    chromedriver_path: str = CHROMEDRIVER_PATH,
    save_json_path: str | None = None,
) -> dict:
    """Search for a match on FotMob, capture headers, fetch matchDetails, return JSON."""
    service = Service(executable_path=chromedriver_path)
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--headless=new")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    driver = webdriver.Chrome(service=service, options=options)

    try:
        driver.get("https://fotmob.com")
        input_element = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Search']"))
        )
        human_type(input_element, search_query)
        input_element.send_keys(Keys.ENTER)

        first_result = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "div.css-1vahj0u-MatchSearchItemCSS a"))
        )
        href_before = first_result.get_attribute("href")

        try:
            _ = driver.get_log("performance")
        except Exception:
            pass

        initial_handles = driver.window_handles.copy()
        initial_url = driver.current_url
        first_result.click()

        try:
            WebDriverWait(driver, 12).until(
                lambda d: len(d.window_handles) > len(initial_handles)
                          or d.current_url != initial_url
                          or (d.execute_script("return location.hash") or "") != ""
            )
        except TimeoutException:
            pass

        if len(driver.window_handles) > len(initial_handles):
            new_handle = [h for h in driver.window_handles if h not in initial_handles][0]
            driver.switch_to.window(new_handle)

        WebDriverWait(driver, 20).until(lambda d: d.execute_script("return document.readyState") == "complete")
        try:
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#main-content")))
        except TimeoutException:
            pass

        time.sleep(0.5)

        try:
            location_hash = driver.execute_script("return location.hash") or ""
        except WebDriverException:
            location_hash = ""
        current_url = driver.current_url
        match_id = (
            extract_match_id(location_hash)
            or extract_match_id(current_url)
            or extract_match_id(href_before)
        )

        if not match_id and href_before:
            driver.get(href_before)
            WebDriverWait(driver, 15).until(lambda d: d.execute_script("return document.readyState") == "complete")
            try:
                location_hash = driver.execute_script("return location.hash") or ""
            except WebDriverException:
                location_hash = ""
            current_url = driver.current_url
            match_id = extract_match_id(location_hash) or extract_match_id(current_url)

        if not match_id:
            page_src = driver.page_source
            match_id = extract_match_id(page_src)

        if not match_id:
            raise ValueError("Could not determine matchId")

        api_url = f"https://www.fotmob.com/api/data/matchDetails?matchId={match_id}"

        found_headers = None
        deadline = time.time() + 20
        checked_messages = set()
        while time.time() < deadline and found_headers is None:
            try:
                logs = driver.get_log("performance")
            except Exception:
                logs = []
            for entry in logs:
                try:
                    msg = json.loads(entry["message"])["message"]
                except Exception:
                    continue
                uid = json.dumps(msg, sort_keys=True)
                if uid in checked_messages:
                    continue
                checked_messages.add(uid)

                if msg.get("method") == "Network.requestWillBeSent":
                    params = msg.get("params", {})
                    req = params.get("request", {})
                    url = req.get("url", "")
                    if "matchDetails" in url and str(match_id) in url:
                        found_headers = req.get("headers", {})
                        break
            if found_headers is None:
                time.sleep(0.4)

        if not found_headers:
            try:
                ua = driver.execute_script("return navigator.userAgent")
            except Exception:
                ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            cookies = driver.get_cookies()
            cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies) if cookies else ""
            request_headers = {
                "User-Agent": ua,
                "Accept": "application/json, text/plain, */*",
                "Referer": current_url or "https://www.fotmob.com",
            }
            if cookie_header:
                request_headers["Cookie"] = cookie_header
        else:
            request_headers = clean_headers(found_headers)

        if "User-Agent" not in request_headers:
            try:
                request_headers["User-Agent"] = driver.execute_script("return navigator.userAgent")
            except Exception:
                request_headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

        resp = requests.get(api_url, headers=request_headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        if save_json_path:
            with open(save_json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        return data
    finally:
        driver.quit()


if __name__ == "__main__":
    out = scrape_match(
        search_query=DEFAULT_SEARCH_QUERY,
        chromedriver_path=CHROMEDRIVER_PATH,
        save_json_path=DEFAULT_SAVE_JSON,
    )
    print(json.dumps({"_summary": "saved", "hasTeams": bool(out.get("general")) if isinstance(out, dict) else None}, indent=2))
