from flask import Flask, request, jsonify
import sqlite3
from main import process_playlists

app = Flask(__name__)

@app.route("/process_playlists", methods=["POST"])
def process_playlists_endpoint():
    playlist_urls = request.json.get("playlist_urls", [])
    if not playlist_urls:
        return jsonify({"error": "No playlist URLs provided"}), 400

    # Call the existing process_playlists function
    process_playlists(playlist_urls)
    return jsonify({"message": "Playlists processed successfully"}), 200


@app.route("/show_deleted_videos", methods=["GET"])
def show_deleted_videos_endpoint():
    conn = sqlite3.connect("youtube_playlists.db")
    c = conn.cursor()

    c.execute("SELECT * FROM deleted ORDER BY playlist_table, id")
    deleted_videos = c.fetchall()
    conn.close()

    if not deleted_videos:
        return jsonify({"message": "No deleted videos found"}), 200

    deleted_videos_list = []
    for video in deleted_videos:
        deleted_videos_list.append(
            {
                "playlist_table": video[1],
                "video_title": video[2],
                "video_author": video[3],
            }
        )

    return jsonify(deleted_videos_list), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
