from pytube import Playlist
import os

# 플레이리스트 URL 리스트 (원하는 만큼 추가 가능)
playlist_urls = [
    "playlist_url",
]

# 각 플레이리스트에 대해 정보 출력 및 파일로 저장
for playlist_url in playlist_urls:
    # Playlist 객체 생성
    playlist = Playlist(playlist_url)

    # 플레이리스트 제목 가져오기
    playlist_title = playlist.title

    # 파일명에 사용할 수 없는 문자 제거 (예: Windows의 경우)
    valid_title = "".join(c for c in playlist_title if c.isalnum() or c in " _-")

    # 파일로 저장할 경로와 이름 설정 (플레이리스트명.txt)
    filename = f"{valid_title}.txt"

    # 파일 쓰기 모드로 열기
    with open(filename, "w", encoding="utf-8") as f:
        # 플레이리스트 제목 쓰기
        f.write(f"{valid_title}\n")
        f.write("-" * 40 + "\n")

        # 플레이리스트에 있는 동영상들을 순회하며 파일에 저장
        for video in playlist.videos:
            # try:
            video_title = video.title
            channel_name = video.author
            music_set = channel_name + " - " + video_title
            print(music_set)
            f.write(f"{music_set}\n")
        # except Exception as e:
        #     print(e)
        # except KeyboardInterrupt:
        #     print("\nKeyboardInterrupt detected. Exiting...")
        #     break

    # 콘솔에 저장 완료 메시지 출력
    print(f"\n'{filename}' 파일로 저장되었습니다.\n")
