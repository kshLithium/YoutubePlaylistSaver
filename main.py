from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException,
)
import sqlite3
import re
import time


# 플레이리스트 제목을 가져오는 함수
def get_playlist_title(driver):
    selectors = [
        "#title > yt-formatted-string.ytd-playlist-sidebar-primary-info-renderer",
        "#text.ytd-playlist-sidebar-primary-info-renderer",
        "h1#title yt-formatted-string",
        "yt-formatted-string#text.ytd-playlist-sidebar-primary-info-renderer",
    ]
    for selector in selectors:
        try:
            title_element = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
            title = title_element.get_attribute("textContent").strip()
            if title and title != "모두 재생":
                return title
        except:
            continue
    return f"Unnamed_Playlist_{int(time.time())}"


# 데이터베이스 테이블을 생성하는 함수
def create_table(c, table_name):
    c.execute(
        f"""CREATE TABLE IF NOT EXISTS "{table_name}"
                    (id INTEGER PRIMARY KEY, video_title TEXT, video_author TEXT)"""
    )


# Selenium WebDriver 설정 함수
def setup_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )
    service = ChromeService(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


# 페이지를 끝까지 스크롤하는 함수
def scroll_to_bottom(driver):
    last_height = driver.execute_script("return document.documentElement.scrollHeight")
    while True:
        driver.execute_script(
            "window.scrollTo(0, document.documentElement.scrollHeight);"
        )
        time.sleep(2)
        new_height = driver.execute_script(
            "return document.documentElement.scrollHeight"
        )
        if new_height == last_height:
            break
        last_height = new_height


# 비디오 항목을 처리하는 함수
def process_video_item(item, index):
    video_title = (
        WebDriverWait(item, 10)
        .until(EC.presence_of_element_located((By.CSS_SELECTOR, "#video-title")))
        .text.strip()
    )
    video_author = (
        WebDriverWait(item, 10)
        .until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "yt-formatted-string#text.ytd-channel-name")
            )
        )
        .text.strip()
    )
    return index, video_title, video_author


# 플레이리스트 정보를 데이터베이스에 저장하는 메인 함수
def save_playlist_to_db(playlist_urls):
    conn = sqlite3.connect("youtube_playlists.db")
    c = conn.cursor()
    driver = setup_driver()

    for playlist_url in playlist_urls:
        try:
            driver.get(playlist_url)
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(5)

            playlist_title = get_playlist_title(driver)
            print(f"\n플레이리스트 제목: {playlist_title}")
            print("=" * 50)

            table_name = re.sub(r"\W+", "_", playlist_title)
            create_table(c, table_name)

            scroll_to_bottom(driver)

            video_items = WebDriverWait(driver, 30).until(
                EC.presence_of_all_elements_located(
                    (By.CSS_SELECTOR, "ytd-playlist-video-renderer")
                )
            )

            for index, item in enumerate(video_items, start=1):
                try:
                    index, video_title, video_author = process_video_item(item, index)
                    c.execute(
                        f'REPLACE INTO "{table_name}" (id, video_title, video_author) VALUES (?, ?, ?)',
                        (index, video_title, video_author),
                    )
                    print(f"{index}. {video_author} : {video_title}")
                    print("-" * 50)
                except (
                    TimeoutException,
                    NoSuchElementException,
                    StaleElementReferenceException,
                ) as e:
                    print(f"Error processing video {index}: {str(e)}")
                    c.execute(
                        f'REPLACE INTO "{table_name}" (id, video_title, video_author) VALUES (?, ?, ?)',
                        (index, "Error: Unable to fetch video info", "Unknown"),
                    )

            conn.commit()
            print(f"\n'{playlist_title}' 플레이리스트가 데이터베이스에 저장되었습니다.")
            print("=" * 50)

        except Exception as e:
            print(f"Error processing playlist {playlist_url}: {str(e)}")

    driver.quit()
    conn.close()


def main():
    with open("playlist_test.txt", "r", encoding="utf-8") as file:
        playlist_urls = [line.strip() for line in file.readlines()]
    save_playlist_to_db(playlist_urls)


if __name__ == "__main__":
    main()
