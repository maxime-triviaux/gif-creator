# Construit depuis la racine du dépôt (l'API réutilise cli/) :
#   docker compose -f api/docker-compose.yml build
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY api/requirements.txt api/
RUN pip install --no-cache-dir -r api/requirements.txt

COPY cli/db.py cli/gif.py cli/search.py cli/
COPY api/app.py api/

RUN useradd --system --uid 10001 app && mkdir /cache && chown app /cache
USER app

ENV DB_PATH=/data/kaamelott.db \
    MEDIA_DIR=/media \
    CACHE_DIR=/cache \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app:app", "--app-dir", "api", "--host", "0.0.0.0", "--port", "8000"]
