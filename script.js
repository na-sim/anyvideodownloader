/**
 * Video Downloader — Frontend logic.
 *
 * Submit form fields (url, format, quality) to POST /api/jobs,
 * poll GET /api/jobs/<id> every ~1.5 seconds, show progress, and
 * reveal a direct <a href download> link to /api/jobs/<id>/file.
 */

(function () {
  "use strict";

  const form         = document.getElementById("download-form");
  const urlInput     = document.getElementById("url-input");
  const qualitySel   = document.getElementById("quality-select");
  const qualityHint  = document.getElementById("quality-hint");
  const dlBtn        = document.getElementById("download-btn");
  const statusArea   = document.getElementById("status-area");
  const progressSec  = document.getElementById("progress-section");
  const doneSec      = document.getElementById("done-section");
  const errorSec     = document.getElementById("error-section");
  const progressFill = document.getElementById("progress-fill");
  const progressPct  = document.getElementById("progress-pct");
  const progressMsg  = document.getElementById("progress-msg");
  const saveBtn      = document.getElementById("save-btn");
  const anotherBtn   = document.getElementById("another-btn");
  const errorMsg     = document.getElementById("error-msg");
  const retryBtn     = document.getElementById("retry-btn");
  const segBtns      = document.querySelectorAll(".seg-btn");

  const QUALITY_OPTIONS = {
    mp4: [
      { value: "",     label: "Select quality", disabled: true },
      { value: "360",  label: "360p" },
      { value: "480",  label: "480p" },
      { value: "720",  label: "720p" },
      { value: "1080", label: "1080p" },
    ],
    mp3: [
      { value: "",    label: "Select audio quality", disabled: true },
      { value: "128", label: "128 kbps" },
      { value: "192", label: "192 kbps" },
      { value: "320", label: "320 kbps" },
    ],
  };

  let currentFormat = "mp4";
  let pollTimer = null;
  let pollRetries = 0;
  const MAX_POLL_RETRIES = 5;

  function show(el) { if (el) el.hidden = false; }
  function hide(el) { if (el) el.hidden = true; }

  function hideAllSections() {
    hide(progressSec);
    hide(doneSec);
    hide(errorSec);
  }

  function setFormDisabled(disabled) {
    urlInput.disabled = disabled;
    qualitySel.disabled = disabled;
    segBtns.forEach(function (b) { b.disabled = disabled; });
    dlBtn.disabled = disabled;
  }

  function updateDownloadBtn() {
    var urlOk = urlInput.value.trim().length > 0;
    var qualOk = qualitySel.value !== "";
    dlBtn.disabled = !(urlOk && qualOk);
  }

  function switchFormat(fmt) {
    currentFormat = fmt;
    segBtns.forEach(function (btn) {
      var selected = btn.dataset.format === fmt;
      btn.classList.toggle("selected", selected);
      btn.setAttribute("aria-pressed", selected ? "true" : "false");
    });

    var options = QUALITY_OPTIONS[fmt];
    qualitySel.innerHTML = "";
    options.forEach(function (opt) {
      var el = document.createElement("option");
      el.value = opt.value;
      el.textContent = opt.label;
      if (opt.disabled) el.disabled = true;
      if (opt.value === "") el.selected = true;
      qualitySel.appendChild(el);
    });
    hide(qualityHint);
    updateDownloadBtn();
  }

  segBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      switchFormat(btn.dataset.format);
    });
  });

  urlInput.addEventListener("input", updateDownloadBtn);

  urlInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !qualitySel.value) {
      e.preventDefault();
      show(qualityHint);
      qualitySel.focus();
    }
  });

  qualitySel.addEventListener("change", function () {
    hide(qualityHint);
    updateDownloadBtn();
  });

  function friendlyHttpError(status, data) {
    if (data && data.error) return data.error;
    if (status === 429) return "Too many requests. Please wait a minute and try again.";
    if (status === 503) return "The server is busy. Please try again in a minute.";
    if (status === 404) return "Job not found or expired. Please try again.";
    return "Request failed (" + status + "). Please try again.";
  }

  function parseResponse(res) {
    return res.text().then(function (txt) {
      var data = {};
      try { data = JSON.parse(txt); } catch (_) {}
      return { ok: res.ok, status: res.status, data: data };
    });
  }

  function startDownload() {
    if (!qualitySel.value) {
      show(qualityHint);
      qualitySel.focus();
      return;
    }

    var url = urlInput.value.trim();
    if (!url) {
      urlInput.focus();
      return;
    }

    setFormDisabled(true);
    hideAllSections();
    show(statusArea);
    show(progressSec);
    progressFill.style.width = "0%";
    progressPct.textContent = "0%";
    progressMsg.textContent = "Queued…";
    pollRetries = 0;

    var params = new URLSearchParams();
    params.set("url", url);
    params.set("format", currentFormat);
    params.set("quality", qualitySel.value);

    fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: params.toString(),
    })
      .then(parseResponse)
      .then(function (r) {
        if (!r.ok) {
          showError(friendlyHttpError(r.status, r.data));
          return;
        }
        startPolling(r.data.job_id);
      })
      .catch(function () {
        showError("Could not reach the server. Check your connection and try again.");
      });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    startDownload();
  });

  function startPolling(jobId) {
    stopPolling();
    pollJob(jobId);
    pollTimer = setInterval(function () { pollJob(jobId); }, 1500);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function pollJob(jobId) {
    fetch("/api/jobs/" + encodeURIComponent(jobId))
      .then(parseResponse)
      .then(function (r) {
        if (!r.ok) {
          if (r.status === 404) {
            stopPolling();
            showError(friendlyHttpError(r.status, r.data));
            return;
          }
          pollRetries += 1;
          if (pollRetries >= MAX_POLL_RETRIES) {
            stopPolling();
            showError(friendlyHttpError(r.status, r.data));
          }
          return;
        }

        var job = r.data;
        pollRetries = 0;

        if (job.status === "queued") {
          hideAllSections();
          show(progressSec);
          progressFill.style.width = "0%";
          progressPct.textContent = "0%";
          progressMsg.textContent = job.message || "Queued…";
          return;
        }

        if (job.status === "downloading") {
          hideAllSections();
          show(progressSec);
          var pct = Number.isFinite(job.progress) ? job.progress : 0;
          progressFill.style.width = pct + "%";
          progressPct.textContent = pct + "%";
          progressMsg.textContent = job.message || "Downloading…";
          return;
        }

        if (job.status === "processing") {
          hideAllSections();
          show(progressSec);
          var ppct = Number.isFinite(job.progress) ? job.progress : 99;
          progressFill.style.width = ppct + "%";
          progressPct.textContent = ppct + "%";
          progressMsg.textContent = job.message || "Converting…";
          return;
        }

        if (job.status === "done") {
          stopPolling();
          hideAllSections();
          show(doneSec);
          var fileUrl = "/api/jobs/" + encodeURIComponent(jobId) + "/file";
          saveBtn.href = fileUrl;
          saveBtn.download = job.filename || "download";

          // Attempt automatic download; browsers/iOS that block it use the Save link
          try {
            var a = document.createElement("a");
            a.href = fileUrl;
            a.download = job.filename || "download";
            a.style.display = "none";
            document.body.appendChild(a);
            a.click();
            setTimeout(function () { document.body.removeChild(a); }, 200);
          } catch (_) {}
          return;
        }

        if (job.status === "error") {
          stopPolling();
          showError(job.error || job.message || "Download failed.");
        }
      })
      .catch(function () {
        pollRetries += 1;
        if (pollRetries >= MAX_POLL_RETRIES) {
          stopPolling();
          showError("Lost connection to the server. Please try again.");
        }
      });
  }

  function showError(msg) {
    stopPolling();
    hideAllSections();
    show(statusArea);
    show(errorSec);
    errorMsg.textContent = msg;
    setFormDisabled(false);
    updateDownloadBtn();
  }

  function resetForm() {
    stopPolling();
    hideAllSections();
    hide(statusArea);
    setFormDisabled(false);
    urlInput.value = "";
    switchFormat("mp4");
    urlInput.focus();
  }

  retryBtn.addEventListener("click", resetForm);
  if (anotherBtn) anotherBtn.addEventListener("click", resetForm);

  switchFormat("mp4");
})();
