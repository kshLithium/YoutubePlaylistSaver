from flask import Flask, request, render_template, redirect, url_for, flash
from main import process_playlists
import sqlite3

app = Flask(__name__)
app.secret_key = "your_secret_key"


# Flask route to render the home page
# '/' URL로 접근 시 index.html 템플릿을 렌더링하여 홈 페이지를 표시합니다.
@app.route("/")
def home():
    deleted_videos = get_deleted_videos()
    return render_template("index.html", deleted_videos=deleted_videos)


# Flask route to handle playlist submission
# '/submit' URL로 POST 요청을 받아 플레이리스트 URL을 처리합니다.
@app.route("/submit", methods=["POST"])
def submit():
    if request.method == "POST":
        # 사용자가 입력한 플레이리스트 URL들을 줄 단위로 가져옵니다.
        playlist_urls = request.form.get("playlist_urls").splitlines()
        # 각 URL 앞뒤의 공백을 제거하고 빈 문자열은 제외합니다.
        playlist_urls = [url.strip() for url in playlist_urls if url.strip()]

        if playlist_urls:
            # 플레이리스트 URL이 유효한 경우 process_playlists 함수를 호출하여 처리합니다.
            process_playlists(playlist_urls)
            flash("플레이리스트 처리가 완료되었습니다.", "success")
        else:
            # 플레이리스트 URL이 유효하지 않은 경우 경고 메시지를 표시합니다.
            flash("유효한 플레이리스트 URL을 입력해주세요.", "warning")

    # 처리 후 홈 페이지로 리디렉션합니다.
    return redirect(url_for("home"))


# 삭제된 동영상 정보를 데이터베이스에서 가져오는 함수
def get_deleted_videos():
    conn = sqlite3.connect("youtube_playlists.db")
    c = conn.cursor()
    c.execute(
        "SELECT playlist_table, video_title, video_author FROM deleted ORDER BY playlist_table, id"
    )
    deleted_videos = c.fetchall()
    conn.close()
    return deleted_videos


# 애플리케이션을 디버그 모드로 실행합니다.
if __name__ == "__main__":
    app.run(debug=True, port=5050)


# 버튼 클릭 이벤트 핸들러 추가
@app.route("/show_deleted", methods=["GET"])
def handle_show_deleted():
    return show_deleted_table()


# HTML 템플릿에 "show deleted" 버튼 추가
# 예시:
# <button onclick="window.location.href='/show_deleted'">Show Deleted</button>


def show_deleted_table():
    # deleted 테이블을 보여주는 로직 추가
    pass  # 여기에 deleted 테이블을 표시하는 코드를 작성하세요.
