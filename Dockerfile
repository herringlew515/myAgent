FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY agent.py server.py storage.py image_upload.py SOFTWARE_GUIDE.md ./
COPY static/ ./static/

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir -p /app/data \
    && chown app:app /app/data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/', timeout=3).close()"

CMD ["sh", "-c", "exec python -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
