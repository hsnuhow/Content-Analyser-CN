# search-extent 容器映像檔
# 需求側情報服務（Google Ads Keyword Planner）。輕量，無 Chrome。
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# 同步、I/O 為主（呼叫 Ads API）；單 worker 多 thread。
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 120 app:app
