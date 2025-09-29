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

# ---- config ----
CHROMEDRIVER_PATH = "./chromedriver"
PLAYER_SEARCH_QUERY = "joao pedro"
SAVE_JSON = "playerdata_joao_pedro.json"
# ----------------

service = Service(executable_path=CHROMEDRIVER_PATH)
options = webdriver.ChromeOptions()
options.add_argument("--start-maximized")
# enable performance logging so we can read Network.requestWillBeSent entries
options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

driver = webdriver.Chrome(service=service, options=options)

# also enable network domain via CDP (helps in some chrome versions)
try:
    driver.execute_cdp_cmd("Network.enable", {})
except Exception:
    pass

def human_type(element, text, min_delay=0.05, max_delay=0.18):
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(min_delay, max_delay))

def extract_player_id(text: str):
    """Try multiple patterns to find a FotMob player id number."""
    if not text:
        return None
    patterns = [
        r"#(\d+)",                       # fragment like #1021382
        r"id=(\d+)",                     # query param id=1021382
        r"/players/(\d+)",               # path like /players/1021382/joao-pedro
        r"/(\d{5,8})(?:/|$|[#?:\-])"    # numeric path segment
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
        # requests expects header values to be strings
        cleaned[str(k)] = str(v)
    return cleaned

try:
    # 1) go to FotMob and search
    driver.get("https://fotmob.com")
    input_element = WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Search']"))
    )
    human_type(input_element, PLAYER_SEARCH_QUERY)
    input_element.send_keys(Keys.ENTER)

    # 2) wait for the first player result and capture its href BEFORE clicking
    # Player results typically have a different CSS selector than match results
    first_result = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href*='/players/']"))
    )
    href_before = first_result.get_attribute("href")
    print("href before click:", href_before)

    # clear performance logs so we only capture fresh network events for the click
    try:
        _ = driver.get_log("performance")
    except Exception:
        pass

    initial_handles = driver.window_handles.copy()
    initial_url = driver.current_url

    # 3) click
    first_result.click()

    # 4) wait for navigation/new tab or hash change
    try:
        WebDriverWait(driver, 12).until(
            lambda d: len(d.window_handles) > len(initial_handles)
                      or d.current_url != initial_url
                      or (d.execute_script("return location.hash") or "") != ""
        )
    except TimeoutException:
        # we'll try fallbacks below
        pass

    # if new tab opened, switch
    if len(driver.window_handles) > len(initial_handles):
        new_handle = [h for h in driver.window_handles if h not in initial_handles][0]
        driver.switch_to.window(new_handle)
        print("switched to new window/tab")

    # ensure the page has time to settle (SPA rendering)
    WebDriverWait(driver, 20).until(lambda d: d.execute_script("return document.readyState") == "complete")
    # optionally wait for a page-specific selector to appear
    try:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#main-content")))
    except TimeoutException:
        pass

    time.sleep(0.5)  # give client-side JS a small moment

    # 5) extract player id from hash/url/href
    try:
        location_hash = driver.execute_script("return location.hash") or ""
    except WebDriverException:
        location_hash = ""
    current_url = driver.current_url
    player_id = extract_player_id(location_hash) or extract_player_id(current_url) or extract_player_id(href_before)

    # fallback: navigate directly and re-check
    if not player_id and href_before:
        print("fallback: navigating directly to href to get playerId")
        driver.get(href_before)
        WebDriverWait(driver, 15).until(lambda d: d.execute_script("return document.readyState") == "complete")
        try:
            location_hash = driver.execute_script("return location.hash") or ""
        except WebDriverException:
            location_hash = ""
        current_url = driver.current_url
        player_id = extract_player_id(location_hash) or extract_player_id(current_url)

    if not player_id:
        # final fallback: try to find numeric id in page source
        page_src = driver.page_source
        player_id = extract_player_id(page_src)

    if not player_id:
        raise ValueError(f"Could not determine playerId. Tried location.hash ({location_hash}), current_url ({current_url}), href ({href_before})")

    print("extracted playerId:", player_id)
    api_url = f"https://www.fotmob.com/api/data/playerData?id={player_id}"
    print("target API URL:", api_url)

    # 6) inspect performance logs for the actual request headers used by the browser for THAT API call
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
            # avoid reprocessing the same message
            uid = json.dumps(msg, sort_keys=True)
            if uid in checked_messages:
                continue
            checked_messages.add(uid)

            # look for the network request event
            if msg.get("method") == "Network.requestWillBeSent":
                params = msg.get("params", {})
                req = params.get("request", {})
                url = req.get("url", "")
                if "playerData" in url and str(player_id) in url:
                    # found the browser request that matches our playerId
                    found_headers = req.get("headers", {})
                    print("found network request URL in logs:", url)
                    break
        if found_headers is None:
            time.sleep(0.4)

    # 7) if we didn't capture headers, build a reasonable fallback (User-Agent, cookies, referer)
    if not found_headers:
        print("Warning: Unable to capture request headers from performance logs — using fallback headers.")
        try:
            ua = driver.execute_script("return navigator.userAgent")
        except Exception:
            ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        # build cookie string from browser cookies
        cookies = driver.get_cookies()  # list of dicts
        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies) if cookies else ""
        fallback = {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Referer": current_url or "https://www.fotmob.com",
        }
        if cookie_header:
            fallback["Cookie"] = cookie_header
        request_headers = fallback
    else:
        request_headers = clean_headers(found_headers)

    # ensure we have a sensible User-Agent
    if "User-Agent" not in request_headers:
        try:
            request_headers["User-Agent"] = driver.execute_script("return navigator.userAgent")
        except Exception:
            request_headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

    # 8) perform the API request using the captured headers
    print("Request headers being used for the API call:")
    for k, v in request_headers.items():
        print(f"    {k}: {v}")

    resp = requests.get(api_url, headers=request_headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    # 9) save the JSON to disk
    with open(SAVE_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"✅ Saved API response to {SAVE_JSON}")

finally:
    driver.quit()