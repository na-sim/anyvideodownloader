FROM python:3.12-slim

# Install ffmpeg, unzip, and Deno JS runtime (used by yt-dlp for YouTube)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- --no-modify-path -y \
    && chmod 755 /usr/local/bin/deno \
    && apt-get purge -y --auto-remove curl unzip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN groupadd -r vduser && useradd -r -g vduser -d /app -s /sbin/nologin vduser

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -U -r requirements.txt

# Copy application code, templates, and static assets (works flat or nested)
COPY . .
RUN mkdir -p templates static \
    && for f in index.html terms.html privacy.html; do if [ -f "$f" ]; then mv "$f" templates/; fi; done \
    && for f in style.css script.js favicon.svg; do if [ -f "$f" ]; then mv "$f" static/; fi; done \
    && test -f templates/index.html && test -f static/style.css

# Ensure the temp directory exists and is writable
RUN mkdir -p /tmp/vd-jobs && chown -R vduser:vduser /tmp/vd-jobs /app

USER vduser

EXPOSE 10000

CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:${PORT:-10000} --worker-class gthread --workers 1 --threads 8 --timeout 120 --access-logfile -"]
