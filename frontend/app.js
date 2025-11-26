const form = document.getElementById("upload-form");
const resultEl = document.getElementById("upload-result");
const statusSection = document.getElementById("status-section");
const statusText = document.getElementById("status-text");

let currentFileId = null;

form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const fileInput = document.getElementById("file-input");
  if (!fileInput.files.length) {
    alert("Please select a file");
    return;
  }

  const formData = new FormData();
  formData.append("file", fileInput.files[0]);

  resultEl.textContent = "Uploading...";
  statusSection.style.display = "none";

  const response = await fetch("/api/upload", {
    method: "POST",
    body: formData,
  });

  const data = await response.json();
  if (!response.ok) {
    resultEl.textContent = `Error: ${data.error || "Upload failed"}`;
    return;
  }

  currentFileId = data.file_id;
  resultEl.textContent = data.message;
  statusSection.style.display = "block";
  statusText.textContent = "Scanning...";

  pollStatus();
});

async function pollStatus() {
  if (!currentFileId) return;

  const interval = setInterval(async () => {
    const res = await fetch(`/api/file-status/${currentFileId}`);
    const data = await res.json();
    if (!res.ok) {
      statusText.textContent = data.error || "Error checking status";
      clearInterval(interval);
      return;
    }

    const status = (data.scan_status || "UNKNOWN").toUpperCase();
    statusText.textContent = `Scan Status: ${status}`;

    if (status === "CLEAN" || status === "INFECTED" || status === "FAILED") {
      clearInterval(interval);
    }
  }, 5000);
}
