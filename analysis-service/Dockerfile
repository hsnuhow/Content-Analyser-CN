# Analysis Pipeline 容器映像檔
# 不包含 Chrome / Selenium（爬蟲已獨立）
# 主要相依：jieba、scikit-learn、Vertex AI（google-genai）、Anthropic
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

WORKDIR /app

# 安裝 Python 相依
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 預先初始化 jieba 字典（避免首次請求時延遲）
RUN python -c "import jieba; jieba.initialize(); print('[jieba] dictionary initialized')"

COPY . .

EXPOSE 8080

# 單 worker，多 thread（分析為 CPU + I/O 混合任務）
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 0 app:app
