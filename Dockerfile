FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/data APP_HOST=0.0.0.0
RUN useradd --create-home --uid 10001 autodo && mkdir /data && chown autodo /data
WORKDIR /app
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY --chown=autodo . .
USER autodo
VOLUME /data
EXPOSE 8501
# UN SEUL worker : sessions, cache du tableau de bord et ordonnanceur sont en mémoire.
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8501/health')" || exit 1
CMD ["python", "main.py"]
