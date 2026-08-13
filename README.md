# YoutubePlaylistSaver

공개 YouTube 플레이리스트의 영상 ID, 제목, 채널, 순서와 재생 상태를 SQLite 스냅샷으로 누적 보관합니다. 이전 스냅샷과 비교해 추가·누락·메타데이터 변경을 기록하므로, 나중에 영상이 삭제되거나 비공개되어도 과거 제목을 추적할 수 있습니다.

## 현재 구조

- `main.py`: 수집, DB 저장, 변경 조회 CLI
- `playlist.txt`: 수집할 공개 플레이리스트 URL 목록
- `youtube_playlists_v2.db`: 실행 시 생성되는 누적 DB(저장소에는 포함하지 않음)
- `tests/`: SQLite 스냅샷과 재생 상태 폴백 테스트

Selenium, Chrome, ChromeDriver는 사용하지 않습니다. `yt-dlp`와 YouTube의 공개 응답만 사용하므로 GPU, CUDA, 브라우저 패키지도 필요하지 않습니다.

## 가장 간단한 실행 방법: Docker

```bash
git clone https://github.com/kshLithium/YoutubePlaylistSaver.git
cd YoutubePlaylistSaver
docker build -t youtube-playlist-saver .
mkdir -p data
docker run --rm -v "$PWD/data:/data" youtube-playlist-saver
```

Windows PowerShell에서는 데이터 폴더를 만든 뒤 다음처럼 실행할 수 있습니다.

```powershell
New-Item -ItemType Directory -Force data
docker build -t youtube-playlist-saver .
docker run --rm -v "${PWD}\data:/data" youtube-playlist-saver
```

DB는 호스트의 `data/youtube_playlists_v2.db`에 남습니다. 기존 DB를 이어서 사용하려면 실행 전에 백업 DB를 이 이름으로 `data` 폴더에 복사하면 됩니다.

Docker 이미지의 기본 명령은 다음과 같습니다.

```bash
python main.py collect --db /data/youtube_playlists_v2.db
```

다른 명령은 이미지 이름 뒤에 지정합니다.

```bash
docker run --rm -v "$PWD/data:/data" youtube-playlist-saver history --db /data/youtube_playlists_v2.db
docker run --rm -v "$PWD/data:/data" youtube-playlist-saver changes --db /data/youtube_playlists_v2.db
docker run --rm -v "$PWD/data:/data" youtube-playlist-saver unavailable --db /data/youtube_playlists_v2.db
```

## Python으로 직접 실행

Python 3.12 기준입니다.

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py collect
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py collect
```

## 공식 YouTube Data API 사용

기본값은 API 키 없이 `yt-dlp`를 사용합니다. YouTube Data API v3 키가 있으면 다음처럼 공식 API를 선택할 수 있습니다.

```bash
export YOUTUBE_API_KEY='...'
python main.py collect --provider youtube-api
```

Docker에서는 환경변수를 전달합니다.

```bash
docker run --rm -e YOUTUBE_API_KEY -v "$PWD/data:/data" youtube-playlist-saver collect --provider youtube-api --db /data/youtube_playlists_v2.db
```

## 데이터 안전장치

- 모든 플레이리스트를 정상 수집한 뒤 한 트랜잭션으로 새 스냅샷을 저장합니다.
- 과거 스냅샷은 수정하거나 삭제하지 않습니다.
- 직전 스냅샷보다 50% 이상 급감하면 기본적으로 저장을 중단합니다.
- YouTube가 대량 재생 확인을 봇 검사로 차단하면, 기존 영상은 직전의 정상 확인 상태를 보존하고 신규 영상은 `unknown`으로 명시합니다. 목록 구조와 변경 이력은 계속 저장하되 재생 가능 여부를 임의로 추측하지 않습니다.
- DB, WAL 파일과 로컬 백업은 Git에 커밋하지 않습니다.

## 플레이리스트와 DB 관리

수집 대상을 바꾸려면 `playlist.txt`를 수정합니다. 이 파일은 새로 clone한 뒤 바로 실행할 수 있도록 저장소에 포함됩니다.

이전 이력을 이어가려면 `youtube_playlists_v2.db`를 보관해야 합니다. DB가 없으면 새 DB를 만들 수 있지만, 과거 영상 제목과 이전 스냅샷 비교 기록은 복구되지 않습니다.

## 테스트

```bash
python -m unittest discover -s tests -v
```
