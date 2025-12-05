// frontend/app.js

// IMPORTANT: align these with your Flask backend routes.
const API_UPLOAD_URL = "api/upload";       // behind ALB, no leading slash
const API_STATUS_URL = "api/scan-status";  // GET ?file_id=...

const POLL_INTERVAL_MS = 3000;

let currentFileId = null;
let pollTimer = null;

document.addEventListener("DOMContentLoaded", () => {
  const uploadForm = document.getElementById("uploadForm");
  const fileInput = document.getElementById("fileInput");
  const dropzone = document.getElementById("dropzone");
  const uploadButton = document.getElementById("uploadButton");
  const clearEventsButton = document.getElementById("clearEventsButton");

  const statusPill = document.getElementById("statusPill");
  const fileNameEl = document.getElementById("fileName");
  const fileIdEl = document.getElementById("fileId");
  const statusTextEl = document.getElementById("statusText");
  const detailTextEl = document.getElementById("detailText");
  const eventsLogEl = document.getElementById("eventsLog");

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function setStatusPill(status) {
    const normalized = (status || "IDLE").toUpperCase();

    const baseClasses = ["status-pill"];
    let variantClass = "status-idle";
    let label = normalized;

    switch (normalized) {
      case "PENDING":
        variantClass = "status-pending";
        label = "PENDING";
        break;
      case "SCANNING":
        variantClass = "status-scanning";
        label = "SCANNING";
        break;
      case "CLEAN":
        variantClass = "status-clean";
        label = "CLEAN";
        break;
      case "INFECTED":
        variantClass = "status-infected";
        label = "INFECTED";
        break;
      case "FAILED":
      case "ERROR":
        variantClass = "status-error";
        label = normalized;
        break;
      default:
        variantClass = "status-idle";
        label = "IDLE";
    }

    statusPill.className = [...baseClasses, variantClass].join(" ");
    statusPill.textContent = label;
  }

  function clearEvents() {
    eventsLogEl.innerHTML =
      '<p class="events-empty">Events cleared. Upload again to see new activity.</p>';
  }

  function renderEvents(events) {
    if (!events || events.length === 0) {
      clearEvents();
      return;
    }

    const items = events
      .map((evt) => {
        const time =
          evt.timestamp ||
          new Date().toISOString().replace("T", " ").slice(0, 19);
        const msg = evt.message || String(evt);
        return `
          <div class="event-item">
            <div class="event-meta">
              <span>${time}</span>
            </div>
            <div class="event-message">${escapeHtml(msg)}</div>
          </div>
        `;
      })
      .join("");

    eventsLogEl.innerHTML = items;
  }

  function setLoading(isLoading) {
    uploadButton.disabled = isLoading;
    uploadButton.textContent = isLoading
      ? "Uploading & scanning…"
      : "Upload & Scan";
  }

  function resetStatus() {
    currentFileId = null;
    clearInterval(pollTimer);
    pollTimer = null;

    fileNameEl.textContent = "—";
    fileIdEl.textContent = "—";
    statusTextEl.textContent = "Waiting for upload…";
    detailTextEl.textContent = "—";
    setStatusPill("IDLE");
    clearEvents();
  }

  function appendEvent(evt) {
    const existingEmpty = eventsLogEl.querySelector(".events-empty");
    if (existingEmpty) existingEmpty.remove();

    const time = new Date().toISOString().replace("T", " ").slice(0, 19);
    const msg = evt.message || String(evt);

    const wrapper = document.createElement("div");
    wrapper.className = "event-item";
    wrapper.innerHTML = `
      <div class="event-meta">
        <span>${time}</span>
      </div>
      <div class="event-message">${escapeHtml(msg)}</div>
    `;

    eventsLogEl.appendChild(wrapper);
    eventsLogEl.scrollTop = eventsLogEl.scrollHeight;
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // ---------------------------------------------------------------------------
  // Upload handler
  // ---------------------------------------------------------------------------

  async function handleUpload(evt) {
    evt.preventDefault();
    if (!fileInput.files || fileInput.files.length === 0) {
      alert("Please choose a file to upload.");
      return;
    }

    const file = fileInput.files[0];
    setLoading(true);
    clearInterval(pollTimer);
    pollTimer = null;
    clearEvents();

    fileNameEl.textContent = file.name;
    statusTextEl.textContent = "Uploading file to backend…";
    detailTextEl.textContent = "—";
    setStatusPill("PENDING");

    try {
      const formData = new FormData();
      formData.append("file", file);

      const resp = await fetch(API_UPLOAD_URL, {
        method: "POST",
        body: formData,
      });

      const responseText = await resp.text(); // read once

      if (!resp.ok) {
        // Try parse JSON, otherwise raw text
        let extraDetail = responseText;
        try {
          const maybeJson = JSON.parse(responseText);
          if (maybeJson.detail || maybeJson.error) {
            extraDetail = maybeJson.detail || maybeJson.error;
          }
        } catch (_) {
          // ignore parse error
        }
        throw new Error(
          `Upload failed with status ${resp.status}. Backend said: ${extraDetail}`
        );
      }

      const data = JSON.parse(responseText || "{}");
      const fileId = data.file_id || data.id || null;

      if (!fileId) {
        throw new Error("Backend response missing file_id.");
      }

      currentFileId = fileId;
      fileIdEl.textContent = fileId;
      statusTextEl.textContent =
        "File uploaded. Waiting for scan to start…";
      appendEvent({
        message: "File uploaded successfully. Scan will start shortly.",
      });

      // start polling
      setStatusPill("PENDING");
      pollTimer = setInterval(pollStatus, POLL_INTERVAL_MS);
      // fire first poll immediately
      pollStatus();
    } catch (err) {
      console.error(err);
      statusTextEl.textContent = "Upload failed.";
      detailTextEl.textContent = err.message || String(err);
      setStatusPill("ERROR");
      appendEvent({ message: "Upload failed – see details above." });
    } finally {
      setLoading(false);
      // clear selection so same file can be re-uploaded
      uploadForm.reset();
    }
  }

  // ---------------------------------------------------------------------------
  // Poll scan status
  // ---------------------------------------------------------------------------

  async function pollStatus() {
    if (!currentFileId) return;

    try {
      const url = `${API_STATUS_URL}?file_id=${encodeURIComponent(
        currentFileId
      )}`;
      const resp = await fetch(url);
      const responseText = await resp.text();

      if (!resp.ok) {
        throw new Error(
          `Status check failed with ${resp.status}. Body: ${responseText}`
        );
      }

      const data = JSON.parse(responseText || "{}");
      const statusRaw = data.status || data.scan_status || "PENDING";
      const status = statusRaw.toUpperCase();
      const detail = data.detail || data.scan_detail || "—";

      statusTextEl.textContent = status;
      detailTextEl.textContent = detail;
      setStatusPill(status);

      if (data.file_name) {
        fileNameEl.textContent = data.file_name;
      }
      if (data.file_id) {
        fileIdEl.textContent = data.file_id;
      }

      if (Array.isArray(data.events)) {
        renderEvents(data.events);
      }

      // Treat these as terminal states and stop polling
      const terminal = ["CLEAN", "INFECTED", "FAILED", "ERROR"];
      if (terminal.includes(status)) {
        clearInterval(pollTimer);
        pollTimer = null;
        appendEvent({
          message: `Scan finished with status: ${status}.`,
        });
      }
    } catch (err) {
      console.error(err);
      appendEvent({
        message: `Error checking scan status: ${
          err.message || String(err)
        }`,
      });
    }
  }

  // ---------------------------------------------------------------------------
  // Drag & drop / UI wiring
  // ---------------------------------------------------------------------------

  dropzone.addEventListener("click", () => fileInput.click());

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dropzone-hover");
  });

  dropzone.addEventListener("dragleave", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dropzone-hover");
  });

  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dropzone-hover");
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      fileInput.files = e.dataTransfer.files;
    }
  });

  clearEventsButton.addEventListener("click", () => {
    clearEvents();
  });

  uploadForm.addEventListener("submit", handleUpload);

  // Initial UI state
  resetStatus();
});
