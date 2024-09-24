from pytube import Playlist
import sqlite3
import re


def save_playlist_to_db(playlist_urls):
    # SQLite 데이터베이스 파일 생성 및 연결
    conn = sqlite3.connect("playlists.db")
    c = conn.cursor()

    # 각 플레이리스트에 대해 정보 출력 및 데이터베이스에 저장
    for playlist_url in playlist_urls:
        playlist = Playlist(playlist_url)
        playlist_title = playlist.title

        # 테이블 이름을 플레이리스트 제목으로 설정 (공백 및 특수문자 제거)
        table_name = re.sub(r"\W+", "_", playlist_title)

        # 테이블 생성 (이미 존재하면 생략)
        c.execute(
            f"""CREATE TABLE IF NOT EXISTS "{table_name}"
                    (id INTEGER, video_title TEXT, video_author TEXT)"""
        )
        # 플레이리스트에 있는 동영상들을 순회하며 정보 저장
        for index, video in enumerate(playlist.videos, start=1):
            video_info = (index, video.title, video.author)
            c.execute(
                f'INSERT INTO "{table_name}" (id, video_title, video_author) VALUES (?, ?, ?)',
                video_info,
            )
            print(f"{index}. {video.author} - {video.title}")

        # 콘솔에 저장 완료 메시지 출력
        print(f"\n'{playlist_title}' 플레이리스트가 데이터베이스에 저장되었습니다.\n")

    # 변경사항 저장 및 연결 종료
    conn.commit()
    conn.close()


# def check_hidden_video(playlist_url):


def main():
    # playlist.txt로부터 플레이리스트 URL 리스트 파싱
    with open("playlist.txt", "r", encoding="utf-8") as file:
        playlist_urls = [line.strip() for line in file.readlines()]

    # for playlist_url in playlist_urls:
    #     if check_has_hidden_video(playlist_url):
    #         print(f"{playlist_url} 플레이리스트에 숨겨진 동영상이 있습니다.")
    #     else:
    #         print(f"{playlist_url} 플레이리스트에 숨겨진 동영상이 없습니다.")

    save_playlist_to_db(playlist_urls)


if __name__ == "__main__":
    main()
