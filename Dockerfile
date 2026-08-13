FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY main.py playlist.txt ./

RUN mkdir -p /data
VOLUME ["/data"]

ENTRYPOINT ["python", "main.py"]
CMD ["collect", "--db", "/data/youtube_playlists_v2.db"]
