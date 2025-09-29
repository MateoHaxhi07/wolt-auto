# --- Dockerfile (fixed for Chrome install without apt-key) ---
FROM python:3.10-slim

# System deps needed by Chrome + Selenium
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl gnupg unzip xvfb \
    libnss3 libxss1 libasound2 libgtk-3-0 libx11-xcb1 libxcb1 libxcomposite1 \
    libxcursor1 libxdamage1 libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 \
    libxtst6 libcups2 fonts-liberation ca-certificates libgbm1 \
 && rm -rf /var/lib/apt/lists/*

# Add Google Chrome repo using signed-by keyring (no apt-key)
RUN mkdir -p /usr/share/keyrings && \
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
      | gpg --dearmor -o /usr/share/keyrings/google-linux-signing-key.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux-signing-key.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
      > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && apt-get install -y --no-install-recommends google-chrome-stable && \
    rm -rf /var/lib/apt/lists/*

# Let Selenium find Chrome
ENV CHROME_BIN=/usr/bin/google-chrome

# Workdir
WORKDIR /app

# Python deps
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . /app

# Run your script by default (Render Cron will use this)
CMD ["python", "wolt_login_magic.py"]
