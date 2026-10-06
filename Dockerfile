FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY airlock ./airlock
COPY site ./site
ENV AIRLOCK_SITE_DIR=/app/site
# Read-only for everyone, whatever modes the build context carried.
RUN chmod -R a+rX,go-w /app && useradd --system --uid 10001 airlock
USER airlock
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2).status==200 else 1)"
CMD ["uvicorn", "airlock.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--no-access-log"]
