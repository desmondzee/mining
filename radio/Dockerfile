# Radio: phone push-to-talk -> brain goals. Build context = repo root.
#   docker compose -f radio/docker-compose.yml up -d --build
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 PORT=8090
WORKDIR /app
COPY radio/requirements.txt /app/radio/requirements.txt
RUN pip install -r /app/radio/requirements.txt
COPY radio/server.py /app/radio/server.py
COPY radio/web /app/radio/web
# world data (/app/data) and keys (/app/radio/secrets) are mounted at run time, never baked in
EXPOSE 8090
HEALTHCHECK --interval=10s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8090/api/radio/health',timeout=2)"
CMD ["python", "/app/radio/server.py"]
