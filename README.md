# YoutubePlaylistSaver

공개 YouTube 플레이리스트의 영상 ID, 제목, 채널, 순서와 재생 상태를 SQLite 스냅샷으로 누적 보관합니다. 이전 스냅샷과 비교해 추가·누락·메타데이터 변경을 기록하므로, 나중에 영상이 삭제되거나 비공개되어도 과거 제목을 추적할 수 있습니다.

## 현재 구조

- `main.py`: 수집, DB 저장, 변경 조회 CLI
- `playlist.txt`: 수집할 공개 플레이리스트 URL 목록
- `youtube_playlists_v2.db`: 실행 시 생성되는 누적 DB(저장소에는 포함하지 않음)
- `tests/`: SQLite 스냅샷과 재생 상태 폴백 테스트

Selenium, Chrome, ChromeDriver는 사용하지 않습니다. `yt-dlp`와 YouTube의 공개 응답만 사용하므로 GPU, CUDA, 브라우저 패키지도 필요하지 않습니다.

## 설치 및 실행

Python 3.12 기준입니다.

```bash
git clone https://github.com/kshLithium/YoutubePlaylistSaver.git
cd YoutubePlaylistSaver
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py collect
```

Windows PowerShell:

```powershell
git clone https://github.com/kshLithium/YoutubePlaylistSaver.git
Set-Location YoutubePlaylistSaver
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py collect
```

기존 이력을 이어서 사용하려면 백업 DB를 저장소 루트의 `youtube_playlists_v2.db`로 복사한 뒤 실행합니다. 이 파일이 없으면 자동으로 새 DB를 만듭니다.

조회 명령은 다음과 같습니다.

```bash
python main.py history
python main.py changes
python main.py unavailable
```

## 공식 YouTube Data API 사용

기본값은 API 키 없이 `yt-dlp`를 사용합니다. YouTube Data API v3 키가 있으면 다음처럼 공식 API를 선택할 수 있습니다.

```bash
export YOUTUBE_API_KEY='...'
python main.py collect --provider youtube-api
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
