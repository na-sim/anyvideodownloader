# Deployment Guide

Step-by-step instructions to deploy Video Downloader on **Render** with a
custom domain behind **Cloudflare**.

---

## 1. Push to GitHub

```bash
cd video-downloader
git init
git checkout -b main
git add -A
git commit -m "Initial commit"
git remote add origin https://github.com/<you>/video-downloader.git
git push -u origin main
```

---

## 2. Create the Render Service

1. Go to [dashboard.render.com](https://dashboard.render.com).
2. Click **New → Web Service**.
3. Connect your GitHub repository.
4. Settings:
   - **Name**: `video-downloader` (or anything you like).
   - **Region**: Choose the closest to your users.
   - **Branch**: `main`.
   - **Runtime**: **Docker**.
   - **Plan**: Starter ($7/mo) recommended.  Free works for testing but
     sleeps after 15 min idle and has only 512 MB RAM.
5. Under **Environment Variables**, add (optional — defaults are fine):
   - `PORT` = `10000`
   - `MAX_CONCURRENT_JOBS` = `2`
6. Click **Create Web Service**.
7. Wait for the build + deploy.  Check the logs for:
   ```
   [INFO] Listening at: http://0.0.0.0:10000
   ```
8. Visit `https://<your-service>.onrender.com/health` — you should see
   `{"status":"ok"}`.

---

## 3. Custom Domain + Cloudflare

### 3a. Add the domain in Render

1. In Render → your service → **Settings → Custom Domains**.
2. Add `yourdomain.com` and `www.yourdomain.com`.
3. Render will show you the CNAME target (something like
   `your-service.onrender.com`).

### 3b. Set up Cloudflare DNS

1. In Cloudflare → your domain → **DNS → Records**.
2. Add two CNAME records:

   | Type  | Name  | Target                          | Proxy   |
   |-------|-------|---------------------------------|---------|
   | CNAME | `@`   | `your-service.onrender.com`     | DNS only (grey cloud) |
   | CNAME | `www` | `your-service.onrender.com`     | DNS only (grey cloud) |

3. **Keep the cloud grey (DNS only)** until Render verifies the domain and
   issues the TLS certificate (takes 1-10 minutes).
4. Once Render shows "Certificate issued", switch both records to
   **Proxied (orange cloud)**.

### 3c. Cloudflare SSL Settings

1. Go to **SSL/TLS → Overview**.
2. Set mode to **Full (strict)**.
3. Go to **SSL/TLS → Edge Certificates**.
4. Turn on **Always Use HTTPS**.

### 3d. Cloudflare Cache Rules (optional)

Create a cache rule:
- **If** URI path starts with `/api/`
- **Then** bypass cache.

This ensures API responses are never cached by Cloudflare.

### 3e. Rate Limiting (optional)

If you have Cloudflare rate limiting available:
- Limit `/api/jobs` POST to 10 requests per minute per IP.

---

## 4. Updating yt-dlp

When downloads start failing (sites change their APIs), yt-dlp needs an
update.  Since it's installed via pip during the Docker build:

1. Go to Render → your service → **Manual Deploy**.
2. Click **Clear build cache & deploy**.

This rebuilds the Docker image and installs the latest yt-dlp.

---

## 5. Troubleshooting

### 524 Timeout Error (Cloudflare)
Cloudflare cuts proxied connections after ~100 seconds.  The app uses
background jobs to avoid this: submit → poll → download.  If you still see
524s, check that the frontend is using the polling flow (not a single long
request).

### "Sign in to confirm you're not a bot"
Some sites detect datacenter IPs and block automated requests.  This is a
platform restriction.  There is no fix other than using a residential IP or
a different source URL.

### Redirect Loop (ERR_TOO_MANY_REDIRECTS)
This usually means Cloudflare SSL mode is set to "Flexible" instead of
"Full (strict)".  Change it in **SSL/TLS → Overview**.

### Out of Memory (OOM) Crashes
1080p downloads + FFmpeg merging can use 300-500 MB of RAM.  On Render's
free plan (512 MB), this may crash.  Upgrade to Starter or higher, or
limit downloads to 720p by setting `MAX_FILESIZE_MB` lower.

### Downloads Fail on iPhone
iOS Safari doesn't support auto-downloads.  Users should tap the **Save to
device** button.  The file opens in Safari's download manager or saves to
the Files app.

### Files Disappear Before Download
Files are deleted after `JOB_TTL_SECONDS` (default 15 minutes).  If
downloads are slow, increase this value.

---

## 6. Server & Account Security Checklist

1. **Render account protection:** Turn on **2-Factor Authentication (2FA)** on
   your Render and GitHub accounts so nobody can log in to your dashboard or
   shell.
2. **Docker isolation:** The container runs as non-root (`vduser`,
   `/sbin/nologin`) with no SSH server and no admin/login endpoints.
3. **Cloudflare protection:** Keep Cloudflare **Proxied (orange cloud)** and
   **Bot Fight Mode** enabled so visitors cannot easily hit the server without
   Cloudflare's WAF and rate limits.
4. **Never commit `.env` or secrets** to Git. Keep `debug=False` in
   production.
