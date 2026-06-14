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

# 下載 jieba 繁體辭典 dict.txt.big（含詞頻，繁中斷詞品質更佳）並預先初始化（避免首次請求延遲）
RUN python -c "import urllib.request; urllib.request.urlretrieve('https://github.com/fxsjy/jieba/raw/master/extra_dict/dict.txt.big', '/app/dict.txt.big')" && \
    python -c "import jieba; jieba.set_dictionary('/app/dict.txt.big'); jieba.initialize(); print('[jieba] big dict initialized')"

COPY . .

EXPOSE 8080

# 單 worker，多 thread（分析為 CPU + I/O 混合任務）
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 0 app:app
