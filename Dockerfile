# 主程式（Web 應用）容器映像檔
# [Note] 爬蟲已拆分為獨立服務 (crawler-service)，主程式不再執行 Chrome，
#        因此本映像檔不需安裝 Chrome / ChromeDriver，可大幅縮小體積與建置時間。
FROM python:3.11-slim

# 設定環境變數
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# 設定工作目錄
WORKDIR /app

# 複製需求文件並安裝 Python 套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案程式碼
COPY . .

# 暴露埠號
EXPOSE 8080

# 啟動指令
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app
