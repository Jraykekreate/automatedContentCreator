import requests
import json
import time
import random

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def human_type(element, text, min_delay=0.05, max_delay=0.18):
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(min_delay, max_delay))


def scrape_image(player_search_query: str, chromedriver_path: str) -> str:
    """
    Scrape Getty Images for a player image and return JSON with image URL.
    """
    service = Service(chromedriver_path)
    options = webdriver.ChromeOptions()
    # options.add_argument("--headless")  # Uncomment for headless operation
    options.add_argument("--disable-gpu")
    driver = webdriver.Chrome(service=service, options=options)

    try:
        driver.get("https://www.gettyimages.com")

        # Type search query
        input_element = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.XPATH, "//input[contains(@placeholder, 'Search the')]")
            )
        )
        human_type(input_element, player_search_query)
        input_element.send_keys(Keys.ENTER)

        # Wait for gallery to load
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.XPATH, "//div[@data-testid='gallery-items-container']")
            )
        )

        # Click Filters
        filters_button = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[@data-testid='search-nav__filters-toggle-edit']")
            )
        )
        filters_button.click()

        # Click "Newest" option
        newest_radio = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, "//div[@id='sortorder-newest']"))
        )
        newest_radio.click()

        # Wait for refreshed gallery
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.XPATH, "//div[@data-testid='gallery-items-container']//img")
            )
        )

        # Get gallery items
        gallery = driver.find_element(
            By.XPATH, "//div[@data-testid='gallery-items-container']"
        )
        items = gallery.find_elements(By.XPATH, ".//div[@data-testid='galleryMosaicAsset']")

        if len(items) < 2:
            raise Exception("Not enough items found after applying filter")

        second_item = items[1]
        img = second_item.find_element(By.XPATH, ".//img")
        core_src = img.get_attribute("src")

        # Return JSON with URL
        return json.dumps({
            "query": player_search_query,
            "image_url": core_src
        })

    finally:
        driver.quit()
