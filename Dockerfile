FROM python:3.12-slim

# Install ffmpeg (required for merging and MP3 conversion)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN groupadd -r vduser && useradd -r -g vduser -d /app -s /sbin/nologin vduser

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -U -r requirements.txt

# Copy application code, templates, and static assets
COPY app.py ./
COPY templates ./templates
COPY static ./static

# Ensure the temp directory exists and is writable
RUN mkdir -p /tmp/vd-jobs && chown -R vduser:vduser /tmp/vd-jobs /app

USER vduser

EXPOSE 10000

CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:${PORT:-10000} --worker-class gthread --workers 1 --threads 8 --timeout 120 --access-logfile -"]
