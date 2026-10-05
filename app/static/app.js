const form = document.querySelector("#download-form");
const button = document.querySelector("#submit-button");
const statusBox = document.querySelector("#status");
const message = document.querySelector("#status-message");
const progressBar = document.querySelector("#progress-bar");
const filesBox = document.querySelector("#files");
const songCount = document.querySelector("#song-count");
const archiveLink = document.querySelector("#archive-link");

function showJob(job) {
  statusBox.hidden = false;
  message.textContent = job.message;
  const total = job.songs_total ?? "—";
  songCount.textContent = `${job.songs_completed} / ${total} tracks`;
  const percent = Number.parseFloat(job.progress);
  const countPercent = job.songs_total ? (job.songs_completed / job.songs_total) * 100 : 0;
  progressBar.style.width = Number.isFinite(percent) ? `${Math.min(percent, 100)}%` : `${Math.max(countPercent, 8)}%`;
  filesBox.replaceChildren();

  job.files.forEach((file) => {
    const item = document.createElement("li");
    item.textContent = file;
    filesBox.append(item);
  });

  if (job.status === "complete") {
    archiveLink.hidden = false;
    archiveLink.href = `/api/jobs/${job.id}/archive`;
    button.disabled = false;
    button.textContent = "Create download";
  }
  if (job.status === "failed") {
    button.disabled = false;
    button.textContent = "Try again";
  }
}

async function pollJob(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`);
  const job = await response.json();
  if (!response.ok) throw new Error(job.detail || "Could not retrieve job status.");
  showJob(job);
  if (job.status === "downloading") window.setTimeout(() => pollJob(jobId).catch(showError), 1500);
}

function showError(error) {
  statusBox.hidden = false;
  message.textContent = error.message || "Something went wrong.";
  progressBar.style.width = "0";
  button.disabled = false;
  button.textContent = "Try again";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  button.disabled = true;
  button.textContent = "Starting…";
  filesBox.replaceChildren();
  archiveLink.hidden = true;
  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: document.querySelector("#url").value,
        mode: document.querySelector("input[name=mode]:checked").value,
      }),
    });
    const job = await response.json();
    if (!response.ok) throw new Error(job.detail || "Could not start the download.");
    showJob(job);
    pollJob(job.id).catch(showError);
  } catch (error) {
    showError(error);
  }
});
