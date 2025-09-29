# Use slim Python base
FROM python:3.10-slim

# Install system deps + Chrome
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg unzip xvfb \
    libnss3 libxss1 libasound2 libgtk-3-0 libx11-xcb1 libxcb1 libxcomposite1 \
    libxcursor1 libxdamage1 libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 \
    libxtst6 libcups2 fonts-liberation ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install Google Chrome stable
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - && \
    echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" \
      > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && apt-get install -y google-chrome-stable && \
    rm -rf /var/lib/apt/lists/*

# Let Selenium find Chrome
ENV CHROME_BIN=/usr/bin/google-chrome

# Workdir
WORKDIR /app

# Copy dependency list and install
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy your project code
COPY . /app

# Default command: run your script
CMD ["python", "wolt_login_magic.py"]
