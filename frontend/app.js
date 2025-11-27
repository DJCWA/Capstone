let pollInterval = null;
let lastDetail = null;

function appendLog(message) {
  const logEl = document.getElementById("scan-log");
  const entry = document.createElement("div");
  entry.className = "log-entry";

  const now = new Date();
  const time = document.createElement("span");
  time.className = "time";
  time.textContent = now.toLocaleTimeString();

  const text = document.createElement("span");
  text.textContent = message;

  entry.appendChild(time);
  entry.appendChild(text);
  logEl.appendChild(entry);
  logEl.scrollTop = logEl.scrollHeight;
}

function updateBadge(status) {
  const badge = document.getElementById("scan-badge");
  if (!status) {
    badge.style.display = "none";
    return;
  }
  badge.style.display = "inline-block";
  badge.textContent = status;
  badge.className = "badge " + status;
}

async function pollStatus(fileKey) {
  try {
    const res = await fetch(`/api/status?key=${encodeURIComponent(fileKey)}`);
    const data = await res.json();

    const statusEl = document.getElementById("scan-status");
    const detailEl = document.getElementById("scan-detail");

    const status = data.status || "PENDING";
    const detail = data.detail || "";

    statusEl.textContent = status;
    detailEl.textContent = detail;
    updateBadge(status);

    if (detail && detail !== lastDetail) {
      appendLog(detail);
      lastDetail = detail;
    }

    if (["CLEAN", "INFECTED", "ERROR"].includes(status)) {
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
    }
  } catch (err) {
    console.error("Error polling status:", err);
    appendLog("Error polling status from backend.");
    const statusEl = document.getElementById("scan-status");
    statusEl.textContent = "ERROR";
    updateBadge("ERROR");
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
  }
}

async function handleUpload(event) {
  event.preventDefault();

  const fileInput = document.getElementById("file-input");
  const button = document.getElementById("upload-button");
  const statusEl = document.getElementById("scan-status");
  const detailEl = document.getElementById("scan-detail");
  const logEl = document.getElementById("scan-log");

  if (!fileInput.files.length) {
    alert("Please select a file first.");
    return;
  }

  const file = fileInput.files[0];

  const formData = new FormData();
  formData.append("file", file);

  button.disabled = true;
  statusEl.textContent = "PENDING";
  detailEl.textContent = "Uploading file to backend…";
  updateBadge("PENDING");
  logEl.innerHTML = "";
  appendLog(`Starting upload of "${file.name}"…`);

  try {
    const res = await fetch("/api/upload", {
      method: "POST",
      body: formData,
    });

    const data = await res.json();

    if (!res.ok) {
      console.error("Upload failed:", data);
      statusEl.textContent = "ERROR";
      detailEl.textContent = data.detail || "Upload failed.";
      updateBadge("ERROR");
      appendLog(`Upload failed: ${data.detail || "Unknown error"}`);
      button.disabled = false;
      return;
    }

    const key = data.key;
    statusEl.textContent = data.status || "PENDING";
    detailEl.textContent = data.detail || "Waiting for scanner Lambda…";
    updateBadge(data.status || "PENDING");
    appendLog("File uploaded to S3. Waiting for scanner Lambda to start…");

    lastDetail = null;

    if (pollInterval) {
      clearInterval(pollInterval);
    }
    pollInterval = setInterval(() => pollStatus(key), 2000);
  } catch (err) {
    console.error("Upload error:", err);
    statusEl.textContent = "ERROR";
    detailEl.textContent = "Unexpected error during upload.";
    updateBadge("ERROR");
    appendLog("Unexpected error during upload.");
  } finally {
    button.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("upload-form");
  form.addEventListener("submit", handleUpload);
});
