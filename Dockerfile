# 使用輕量級的 Python 3.11 基礎映像檔
FROM python:3.11-slim

# 設定環境變數
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# 設定 Chrome 相關環境變數
ENV CHROME_BIN=/usr/bin/google-chrome
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# 安裝系統相依套件與 Chrome/ChromeDriver
# [Note] undetected-chromedriver 需要 patch chromedriver binary，所以我們必須下載它
# 下面的腳本會：
# 1. 安裝 Google Chrome Stable
# 2. 獲取安裝的 Chrome 版本
# 3. 下載對應版本的 ChromeDriver 並放到 /usr/bin/chromedriver
# 4. [FIX] 安裝 libglib2.0-0, libnss3 等相依庫，解決 status code 127 問題
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    jq \
    libglib2.0-0 \
    libnss3 \
    libgconf-2-4 \
    libfontconfig1 \
    libxi6 \
    libxcursor1 \
    libxss1 \
    libxcomposite1 \
    libasound2 \
    libxdamage1 \
    libxtst6 \
    libatk1.0-0 \
    libgtk-3-0 \
    --no-install-recommends \
    && \
    # [FIX] 使用新的 GPG 金鑰管理方式 (Signed-By)
    wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list && \
    # 安裝 Google Chrome
    apt-get update && apt-get install -y google-chrome-stable && \
    # 獲取 Chrome 版本 (例如: 122.0.6261.94)
    CHROME_VERSION=$(google-chrome --version | grep -oE "[0-9.]+") && \
    echo "Installed Chrome Version: $CHROME_VERSION" && \
    # 下載對應的 ChromeDriver (使用 Google 的 JSON API 查找)
    # 這裡我們只取大版本號來查找 latest patch，以確保相容性
    CHROME_MAJOR_VERSION=$(echo "$CHROME_VERSION" | cut -d. -f1) && \
    # 對於 Chrome 115+，使用新的 for-testing API
    DRIVER_URL=$(curl -s "https://googlechromelabs.github.io/chrome-for-testing/latest-versions-per-milestone-with-downloads.json" | jq -r ".milestones.\"$CHROME_MAJOR_VERSION\".downloads.chromedriver[] | select(.platform==\"linux64\") | .url") && \
    echo "Downloading ChromeDriver from: $DRIVER_URL" && \
    wget -q -O /tmp/chromedriver.zip "$DRIVER_URL" && \
    unzip /tmp/chromedriver.zip -d /tmp/ && \
    mv /tmp/chromedriver-linux64/chromedriver /usr/bin/chromedriver && \
    chmod +x /usr/bin/chromedriver && \
    # 驗證安裝
    google-chrome --version && \
    chromedriver --version && \
    # 清理
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/*

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
