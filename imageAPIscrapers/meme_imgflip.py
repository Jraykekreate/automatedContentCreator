import json
import time
import random

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


def human_type(element, text, min_delay=0.05, max_delay=0.18):
    """Simulates human typing into an input element."""
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(min_delay, max_delay))


def scrape_meme(memeQuery: str, chromedriver_path: str) -> str:
    """
    Scrape Imgflip meme generator quickly (skip CSS load) and return JSON with meme image URL.
    """
    service = Service(chromedriver_path)
    options = webdriver.ChromeOptions()
    options.page_load_strategy = "none"   # don't wait for full page load
    # options.add_argument("--headless")  # uncomment if needed
    options.add_argument("--disable-gpu")

    driver = webdriver.Chrome(service=service, options=options)

    # 🚫 Block CSS files
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": ["*.css"]})

    try:
        print("Navigating to Imgflip...")
        driver.get("https://imgflip.com/memegenerator")

        wait = WebDriverWait(driver, 20)

        # Only wait for search input
        input_element = wait.until(
            EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Search all memes']"))
        )
        print("Input element found. Typing...")
        input_element.clear()
        human_type(input_element, memeQuery)
        time.sleep(0.6)

        # Wait for dropdown results (with fallback if needed)
        try:
            results = WebDriverWait(driver, 8).until(
                EC.visibility_of_any_elements_located((By.CSS_SELECTOR, ".mm-search-result-text"))
            )
        except TimeoutException:
            print("Dropdown didn't appear — triggering JS fallback...")
            driver.execute_script(
                "arguments[0].value = arguments[1];"
                "arguments[0].dispatchEvent(new Event('input', { bubbles: true }));",
                input_element, memeQuery
            )
            results = WebDriverWait(driver, 8).until(
                EC.visibility_of_any_elements_located((By.CSS_SELECTOR, ".mm-search-result-text"))
            )

        # Click the first result
        first_result = results[0]
        driver.execute_script("arguments[0].click();", first_result)

        # Wait for meme image
        img_element = wait.until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "img.mm-img.shadow"))
        )
        img_url = img_element.get_attribute("src")
        print("Image URL found:", img_url)

        return json.dumps({
            "query": memeQuery,
            "image_url": img_url
        })

    finally:
        driver.quit()
