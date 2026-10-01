FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/data/models
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app
RUN useradd -u 1000 -m rag && mkdir -p /data && chown rag /data
USER rag
EXPOSE 8790
HEALTHCHECK --interval=60s --timeout=10s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8790/health',timeout=8)"
CMD ["python", "-m", "app.server"]
