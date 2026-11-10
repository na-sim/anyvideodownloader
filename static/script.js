/**
 * AnyVideo — Frontend logic.
 *
 * Flow: Paste/enter URL -> POST /api/info shows thumbnail, title, duration
 * & real available qualities -> pick quality -> POST /api/jobs -> poll -> Save.
 */

(function () {
  "use strict";

  const form               = document.getElementById("download-form");
  const urlInput           = document.getElementById("url-input");
  const pasteBtn           = document.getElementById("paste-btn");
  const urlHelper          = document.getElementById("url-helper");
  const formatCards        = document.querySelectorAll(".format-card");
  const videoPreview       = document.getElementById("video-preview");
  const previewThumb       = document.getElementById("preview-thumb");
  const previewTitle       = document.getElementById("preview-title");
  const previewSource      = document.getElementById("preview-source");
  const previewDuration    = document.getElementById("preview-duration");
  const qualityPlaceholder = document.getElementById("quality-placeholder");
  const qualityLoading     = document.getElementById("quality-loading");
  const infoError          = document.getElementById("info-error");
  const infoErrorMsg       = document.getElementById("info-error-msg");
  const qualityActive      = document.getElementById("quality-active");
  const qualityLabel       = document.getElementById("quality-label");
  const qualityChips       = document.getElementById("quality-chips");
  const qualitySel         = document.getElementById("quality-select");
  const qualityHint        = document.getElementById("quality-hint");
  const dlBtn              = document.getElementById("download-btn");
  const dlBtnText          = document.getElementById("download-btn-text");
  const statusArea         = document.getElementById("status-area");
  const progressSec        = document.getElementById("progress-section");
  const spinnerSec         = document.getElementById("spinner-section");
  const doneSec            = document.getElementById("done-section");
  const errorSec           = document.getElementById("error-section");
  const progressFill       = document.getElementById("progress-fill");
  const progressPct        = document.getElementById("progress-pct");
  const progressMsg        = document.getElementById("progress-msg");
  const spinnerMsg         = document.getElementById("spinner-msg");
  const saveBtn            = document.getElementById("save-btn");
  const anotherBtn         = document.getElementById("another-btn");
  const errorMsg           = document.getElementById("error-msg");
  const retryBtn           = document.getElementById("retry-btn");

  const MP3_BITRATES = ["128", "192", "320"];
  const DEFAULT_HELPER = "Use a public link to content you have permission to download.";

  let currentFormat = "mp4";
  let selectedQuality = "";
  let loadedInfo = null;
  let inspectedUrl = "";
  let inspectTimer = null;
  let inspectController = null;
  let pollTimer = null;
  let pollRetries = 0;
  const MAX_POLL_RETRIES = 5;

  function show(el) { if (el) el.hidden = false; }
  function hide(el) { if (el) el.hidden = true; }

  function formatDuration(sec) {
    if (!Number.isFinite(sec) || sec < 0) return "Duration unknown";
    var total = Math.round(sec);
    var hrs = Math.floor(total / 3600);
    var mins = Math.floor((total % 3600) / 60);
    var secs = total % 60;
    var pad = function (n) { return n < 10 ? "0" + n : String(n); };
    if (hrs > 0) return hrs + ":" + pad(mins) + ":" + pad(secs);
    return mins + ":" + pad(secs);
  }

  function hideAllSections() {
    hide(progressSec);
    hide(spinnerSec);
    hide(doneSec);
    hide(errorSec);
  }

  function setFormDisabled(disabled) {
    urlInput.disabled = disabled;
    if (pasteBtn) pasteBtn.disabled = disabled;
    formatCards.forEach(function (c) { c.disabled = disabled; });
    qualityChips.querySelectorAll(".q-chip").forEach(function (b) { b.disabled = disabled; });
    dlBtn.disabled = disabled;
  }

  function updateDownloadBtn() {
    var urlOk = urlInput.value.trim().length > 0 && loadedInfo !== null;
    var qualOk = selectedQuality !== "";
    dlBtn.disabled = !(urlOk && qualOk);
  }

  function clearInspection() {
    if (inspectTimer) {
      clearTimeout(inspectTimer);
      inspectTimer = null;
    }
    if (inspectController) {
      inspectController.abort();
      inspectController = null;
    }
    loadedInfo = null;
    inspectedUrl = "";
    selectedQuality = "";
    qualitySel.innerHTML = "<option value='' disabled selected>Select quality</option>";
    qualityChips.innerHTML = "";
    hide(videoPreview);
    hide(previewThumb);
    previewThumb.removeAttribute("src");
    hide(qualityLoading);
    hide(infoError);
    hide(qualityActive);
    hide(qualityHint);
    show(qualityPlaceholder);
    updateDownloadBtn();
  }

  function selectQuality(val) {
    selectedQuality = val || "";
    qualitySel.value = selectedQuality;
    qualityChips.querySelectorAll(".q-chip").forEach(function (chip) {
      var active = chip.dataset.value === selectedQuality;
      chip.classList.toggle("selected", active);
      chip.setAttribute("aria-pressed", active ? "true" : "false");
    });
    if (selectedQuality) hide(qualityHint);
    updateDownloadBtn();
  }

  function renderQualityOptions() {
    selectedQuality = "";
    hide(qualityHint);
    qualityChips.innerHTML = "";
    qualitySel.innerHTML = "";

    var ph = document.createElement("option");
    ph.value = "";
    ph.textContent = currentFormat === "mp4" ? "Select quality" : "Select audio quality";
    ph.disabled = true;
    ph.selected = true;
    qualitySel.appendChild(ph);

    if (!loadedInfo) {
      updateDownloadBtn();
      return;
    }

    qualityLabel.textContent =
      currentFormat === "mp4" ? "Select video quality" : "Select audio quality";

    if (currentFormat === "mp4") {
      var list = Array.isArray(loadedInfo.qualities) ? loadedInfo.qualities : [];
      if (!list.length) {
        show(infoError);
        infoErrorMsg.textContent = "No MP4 video qualities found for this link. Try Audio (MP3).";
        hide(qualityActive);
        updateDownloadBtn();
        return;
      }
      hide(infoError);
      show(qualityActive);
      list.forEach(function (q) {
        var val = String(q);
        var opt = document.createElement("option");
        opt.value = val;
        opt.textContent = val + "p";
        qualitySel.appendChild(opt);

        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "q-chip";
        btn.dataset.value = val;
        btn.textContent = val + "p";
        btn.setAttribute("aria-pressed", "false");
        btn.addEventListener("click", function () { selectQuality(val); });
        qualityChips.appendChild(btn);
      });
    } else {
      if (!loadedInfo.hasAudio) {
        show(infoError);
        infoErrorMsg.textContent = "No audio track found in this link.";
        hide(qualityActive);
        updateDownloadBtn();
        return;
      }
      hide(infoError);
      show(qualityActive);
      MP3_BITRATES.forEach(function (rate) {
        var opt = document.createElement("option");
        opt.value = rate;
        opt.textContent = rate + " kbps";
        qualitySel.appendChild(opt);

        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "q-chip";
        btn.dataset.value = rate;
        btn.textContent = rate + " kbps";
        btn.setAttribute("aria-pressed", "false");
        btn.addEventListener("click", function () { selectQuality(rate); });
        qualityChips.appendChild(btn);
      });
    }

    updateDownloadBtn();
  }

  function showPreview(data) {
    previewTitle.textContent = data.title || "Untitled video";
    if (data.source) {
      previewSource.textContent = data.source;
      show(previewSource);
    } else {
      hide(previewSource);
    }
    previewDuration.textContent = "Duration: " + formatDuration(data.duration);

    if (data.thumbnail) {
      previewThumb.onload = function () { show(previewThumb); };
      previewThumb.onerror = function () { hide(previewThumb); };
      previewThumb.src = data.thumbnail;
    } else {
      hide(previewThumb);
      previewThumb.removeAttribute("src");
    }

    show(videoPreview);
  }

  function inspectUrl(url) {
    if (!url || url === inspectedUrl && loadedInfo) return;

    if (inspectController) inspectController.abort();
    inspectController = new AbortController();
    inspectedUrl = url;
    loadedInfo = null;
    selectedQuality = "";
    updateDownloadBtn();

    hide(qualityPlaceholder);
    hide(infoError);
    hide(qualityActive);
    hide(videoPreview);
    show(qualityLoading);

    var params = new URLSearchParams();
    params.set("url", url);

    fetch("/api/info", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: params.toString(),
      signal: inspectController.signal,
    })
      .then(function (res) {
        return res.text().then(function (txt) {
          var data = {};
          try { data = JSON.parse(txt); } catch (_) {}
          return { ok: res.ok, data: data };
        });
      })
      .then(function (r) {
        hide(qualityLoading);
        if (!r.ok) {
          loadedInfo = null;
          hide(videoPreview);
          hide(qualityActive);
          show(infoError);
          infoErrorMsg.textContent = r.data.error || "Could not check this link.";
          updateDownloadBtn();
          return;
        }
        loadedInfo = r.data;
        showPreview(r.data);
        renderQualityOptions();
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        hide(qualityLoading);
        loadedInfo = null;
        show(infoError);
        infoErrorMsg.textContent = "Could not reach the server to check this link.";
        updateDownloadBtn();
      });
  }

  function scheduleInspect(immediate) {
    var val = urlInput.value.trim();
    if (inspectTimer) {
      clearTimeout(inspectTimer);
      inspectTimer = null;
    }
    if (!val) {
      clearInspection();
      return;
    }
    if (!/^https?:\/\/[^/\s]+\.[^/\s]+/i.test(val)) {
      if (loadedInfo || val !== inspectedUrl) {
        clearInspection();
      }
      return;
    }
    if (immediate) {
      inspectUrl(val);
    } else {
      inspectTimer = setTimeout(function () { inspectUrl(val); }, 450);
    }
  }

  function switchFormat(fmt) {
    currentFormat = fmt;
    dlBtnText.textContent = fmt === "mp4" ? "Download MP4" : "Download MP3";

    formatCards.forEach(function (card) {
      var active = card.dataset.format === fmt;
      card.classList.toggle("selected", active);
      card.setAttribute("aria-pressed", active ? "true" : "false");
    });

    if (loadedInfo) {
      renderQualityOptions();
    } else {
      selectedQuality = "";
      updateDownloadBtn();
    }
  }

  formatCards.forEach(function (card) {
    card.addEventListener("click", function () {
      switchFormat(card.dataset.format);
    });
  });

  if (pasteBtn) {
    pasteBtn.addEventListener("click", function () {
      if (navigator.clipboard && navigator.clipboard.readText) {
        navigator.clipboard.readText()
          .then(function (text) {
            if (text && text.trim()) {
              urlInput.value = text.trim();
              urlHelper.textContent = DEFAULT_HELPER;
              scheduleInspect(true);
            } else {
              urlInput.focus();
            }
          })
          .catch(function () {
            urlInput.focus();
            urlHelper.textContent = "Clipboard access denied — press Ctrl+V inside the box.";
          });
      } else {
        urlInput.focus();
      }
    });
  }

  urlInput.addEventListener("paste", function () {
    setTimeout(function () { scheduleInspect(true); }, 30);
  });

  urlInput.addEventListener("input", function () {
    urlHelper.textContent = DEFAULT_HELPER;
    scheduleInspect(false);
  });

  urlInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      var val = urlInput.value.trim();
      if (!val) return;
      if (!loadedInfo) {
        e.preventDefault();
        inspectUrl(val);
        return;
      }
      if (!selectedQuality) {
        e.preventDefault();
        show(qualityHint);
        var firstChip = qualityChips.querySelector(".q-chip");
        if (firstChip) firstChip.focus();
      }
    }
  });

  function startDownload() {
    var url = urlInput.value.trim();
    if (!url) {
      urlInput.focus();
      return;
    }
    if (!loadedInfo) {
      inspectUrl(url);
      return;
    }
    if (!selectedQuality) {
      show(qualityHint);
      var firstChip = qualityChips.querySelector(".q-chip");
      if (firstChip) firstChip.focus();
      return;
    }

    setFormDisabled(true);
    hideAllSections();
    show(statusArea);
    show(progressSec);
    show(spinnerSec);
    progressFill.style.width = "0%";
    progressPct.textContent = "0%";
    progressMsg.textContent = "Queued…";
    spinnerMsg.textContent = "Starting…";
    pollRetries = 0;

    var params = new URLSearchParams();
    params.set("url", url);
    params.set("format", currentFormat);
    params.set("quality", selectedQuality);

    fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: params.toString(),
    })
      .then(function (res) {
        return res.text().then(function (txt) {
          var data = {};
          try { data = JSON.parse(txt); } catch (_) {}
          return { ok: res.ok, status: res.status, data: data };
        });
      })
      .then(function (r) {
        if (!r.ok) {
          showError(r.data.error || ("Request failed (" + r.status + "). Please try again."));
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
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (job) {
        pollRetries = 0;

        if (job.status === "queued") {
          hideAllSections();
          show(progressSec);
          show(spinnerSec);
          progressFill.style.width = "0%";
          progressPct.textContent = "0%";
          progressMsg.textContent = job.message || "Queued…";
          spinnerMsg.textContent = "Queued…";
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
          show(spinnerSec);
          var ppct = Number.isFinite(job.progress) ? job.progress : 99;
          progressFill.style.width = ppct + "%";
          progressPct.textContent = ppct + "%";
          progressMsg.textContent = job.message || "Converting…";
          spinnerMsg.textContent = job.message || "Converting…";
          return;
        }

        if (job.status === "done") {
          stopPolling();
          hideAllSections();
          show(doneSec);
          var fileUrl = "/api/jobs/" + encodeURIComponent(jobId) + "/file";
          saveBtn.href = fileUrl;
          saveBtn.download = job.filename || "download";

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
    urlHelper.textContent = DEFAULT_HELPER;
    clearInspection();
    switchFormat("mp4");
    urlInput.focus();
  }

  retryBtn.addEventListener("click", resetForm);
  if (anotherBtn) anotherBtn.addEventListener("click", resetForm);

  switchFormat("mp4");
})();
