import os
import sqlite3
import re
import time
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


# 플레이리스트 정보를 데이터베이스에 저장하는 함수
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

            # 숨겨진 동영상 수 확인
            hidden_videos_count = find_hidden_videos(driver)
            if hidden_videos_count > 0:
                print(
                    f"이 플레이리스트에는 {hidden_videos_count}개의 숨겨진 동영상이 있습니다.",
                    end="",
                )

            playlist_title = get_playlist_title(driver)
            print(f"\n플레이리스트 제목: {playlist_title}")
            print("=" * 80)

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
            print("=" * 80)
            print()

        except Exception as e:
            print(f"Error processing playlist {playlist_url}: {str(e)}")

    driver.quit()
    conn.close()


def find_hidden_videos(driver, timeout=10):
    """
    플레이리스트 페이지에서 숨겨진 동영상의 수를 찾는 함수

    :param driver: Selenium WebDriver 인스턴스
    :param timeout: 요소를 찾기 위한 대기 시간 (초)
    :return: 숨겨진 동영상의 수, 알림이 없으면 0 반환
    """
    try:
        # 알림 요소를 찾습니다
        alert = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "ytd-alert-with-button-renderer")
            )
        )

        # 알림 텍스트를 가져옵니다
        alert_text = alert.find_element(By.ID, "text").text

        # 숨겨진 동영상의 수를 추출합니다
        import re

        match = re.search(r"사용할 수 없는 동영상 (\d+)개가 숨겨졌습니다", alert_text)
        if match:
            return int(match.group(1))
        else:
            print("알림은 있지만 예상된 형식이 아닙니다.")
            return 0

    except TimeoutException:
        return 0
    except NoSuchElementException:
        print("알림 요소의 구조가 예상과 다릅니다.")
        return 0
    except Exception as e:
        print(f"숨겨진 동영상을 찾는 중 오류 발생: {str(e)}")
        return 0


def check_and_update_playlists(playlist_urls):
    conn = sqlite3.connect("youtube_playlists.db")
    c = conn.cursor()
    driver = setup_driver()

    # 삭제된 동영상을 위한 테이블 생성
    c.execute(
        """CREATE TABLE IF NOT EXISTS deleted
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  playlist_table TEXT,
                  video_title TEXT,
                  video_author TEXT)"""
    )

    for playlist_url in playlist_urls:
        try:
            driver.get(playlist_url)
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(5)

            playlist_title = get_playlist_title(driver)
            table_name = re.sub(r"\W+", "_", playlist_title)

            hidden_videos_count = find_hidden_videos(driver)
            if hidden_videos_count > 0:
                print(
                    f"\n플레이리스트 '{playlist_title}'에 숨겨진 동영상이 {hidden_videos_count}개 있습니다."
                )

            # 데이터베이스에서 모든 비디오 정보를 가져옵니다
            c.execute(f'SELECT id, video_title, video_author FROM "{table_name}"')
            db_videos = c.fetchall()

            scroll_to_bottom(driver)
            visible_video_items = WebDriverWait(driver, 30).until(
                EC.presence_of_all_elements_located(
                    (By.CSS_SELECTOR, "ytd-playlist-video-renderer")
                )
            )

            visible_video_ids = set()
            for index, item in enumerate(visible_video_items, start=1):
                try:
                    _, video_title, video_author = process_video_item(item, index)
                    visible_video_ids.add((video_title, video_author))
                except (
                    TimeoutException,
                    NoSuchElementException,
                    StaleElementReferenceException,
                ):
                    pass

            # 삭제된 동영상 처리
            deleted_videos = []
            for video in db_videos:
                if (video[1], video[2]) not in visible_video_ids:
                    deleted_videos.append(video)
                    c.execute(
                        "INSERT INTO deleted (playlist_table, video_title, video_author) VALUES (?, ?, ?)",
                        (table_name, video[1], video[2]),
                    )

            if deleted_videos:
                print("삭제된 동영상:")
                for video in deleted_videos:
                    print(f"{video[0]}. {video[2]} : {video[1]}")
                print(f"\n삭제된 동영상들이 'deleted' 테이블에 저장되었습니다.")

            # 플레이리스트 테이블 업데이트
            c.execute(f'DELETE FROM "{table_name}"')

            for index, item in enumerate(visible_video_items, start=1):
                try:
                    index, video_title, video_author = process_video_item(item, index)
                    c.execute(
                        f'INSERT INTO "{table_name}" (id, video_title, video_author) VALUES (?, ?, ?)',
                        (index, video_title, video_author),
                    )
                    print(f"{index}. {video_author} : {video_title}")
                except (
                    TimeoutException,
                    NoSuchElementException,
                    StaleElementReferenceException,
                ) as e:
                    print(f"Error processing video {index}: {str(e)}")
                    c.execute(
                        f'INSERT INTO "{table_name}" (id, video_title, video_author) VALUES (?, ?, ?)',
                        (index, "Error: Unable to fetch video info", "Unknown"),
                    )

            conn.commit()
            print(f"\n'{playlist_title}' 플레이리스트가 업데이트되었습니다.")
            print("=" * 80)
            print()

        except Exception as e:
            print(f"Error processing playlist {playlist_url}: {str(e)}")

    driver.quit()
    conn.close()


def main():
    with open("playlist_test.txt", "r", encoding="utf-8") as file:
        playlist_urls = [line.strip() for line in file.readlines()]

    if not os.path.exists("youtube_playlists.db"):
        print("데이터베이스가 존재하지 않습니다. 새로 생성합니다.")
        save_playlist_to_db(playlist_urls)
    else:
        print("데이터베이스가 이미 존재합니다. 플레이리스트를 검사하고 업데이트합니다.")
        check_and_update_playlists(playlist_urls)


if __name__ == "__main__":
    main()
