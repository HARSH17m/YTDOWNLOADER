import os
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yt_dlp
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request


BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_ROOT = Path(os.getenv("DOWNLOAD_DIR", "/tmp/music-downloads"))
RETENTION_SECONDS = int(os.getenv("RETENTION_MINUTES", "60")) * 60
DEFAULT_ALLOWED_HOSTS = "youtube.com,youtu.be"
ALLOWED_HOSTS = tuple(
    host.strip().lower()
    for host in os.getenv("ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS).split(",")
    if host.strip()
)

app = FastAPI(title="Personal Music Downloader")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


class DownloadRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2_048)
    mode: Literal["single", "playlist"] = "single"


def is_allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not hostname:
        return False
    return any(hostname == allowed or hostname.endswith(f".{allowed}") for allowed in ALLOWED_HOSTS)


def cleanup_expired_jobs() -> None:
    """Remove completed or failed jobs after the configured retention period."""
    cutoff = time.time() - RETENTION_SECONDS
    with jobs_lock:
        expired_ids = [
            job_id for job_id, job in jobs.items()
            if job["created_at"] < cutoff and job["status"] != "downloading"
        ]
        for job_id in expired_ids:
            job = jobs.pop(job_id)
            shutil.rmtree(job["directory"], ignore_errors=True)


def public_job(job: dict) -> dict:
    return {
        "id": job["id"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "files": job["files"],
        "songs_completed": job["songs_completed"],
        "songs_total": job["songs_total"],
        "archive": job["archive"],
    }


def update_job(job_id: str, **changes) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(changes)


def audio_files(job_directory: Path) -> list[str]:
    return sorted(path.name for path in job_directory.glob("*.mp3") if path.is_file())


def sync_completed_songs(job_id: str, job_directory: Path) -> list[str]:
    files = audio_files(job_directory)
    with jobs_lock:
        job = jobs.get(job_id)
        if job is not None:
            job["files"] = files
            job["songs_completed"] = len(files)
    return files


def discover_song_total(url: str, mode: str) -> int | None:
    if mode == "single":
        return 1
    options = {"extract_flat": True, "skip_download": True, "quiet": True}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = info.get("entries") if info else None
        if entries is not None:
            return sum(1 for entry in entries if entry)
    except Exception:
        # The actual download can still succeed even when playlist metadata is unavailable.
        pass
    return None


def create_archive(job_directory: Path, files: list[str]) -> str:
    archive_name = "music-downloads.zip"
    archive_path = job_directory / archive_name
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in files:
            archive.write(job_directory / filename, arcname=filename)
    return archive_name


def download_job(job_id: str, url: str, mode: str, job_directory: Path) -> None:
    total = discover_song_total(url, mode)
    update_job(job_id, songs_total=total, message="Reading track list…")

    def progress_hook(data: dict) -> None:
        if data.get("status") == "downloading":
            percent = data.get("_percent_str", "").strip()
            update_job(job_id, progress=percent or "Working…", message="Downloading audio…")
        elif data.get("status") == "finished":
            update_job(job_id, progress="Processing", message="Converting to MP3…")

    def postprocessor_hook(data: dict) -> None:
        if data.get("status") == "finished":
            files = sync_completed_songs(job_id, job_directory)
            update_job(
                job_id,
                message=f"Converted {len(files)} track(s). Preparing the next track…",
            )

    options = {
        "format": "bestaudio/best",
        # The media ID keeps filenames unique even if two tracks share a title.
        "outtmpl": str(job_directory / "%(title)s [%(id)s].%(ext)s"),
        "restrictfilenames": True,
        "noplaylist": mode == "single",
        "ignoreerrors": mode == "playlist",
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            result = ydl.download([url])

        files = sync_completed_songs(job_id, job_directory)
        if result != 0 or not files:
            update_job(job_id, status="failed", progress="", message="No audio file could be created.")
            return

        archive_name = create_archive(job_directory, files)

        update_job(
            job_id,
            status="complete",
            progress="100%",
            message=f"ZIP ready with {len(files)} track(s). It expires in {RETENTION_SECONDS // 60} minutes.",
            files=files,
            songs_completed=len(files),
            songs_total=total if total is not None else len(files),
            archive=archive_name,
        )
    except Exception as error:
        update_job(job_id, status="failed", progress="", message=f"Download failed: {str(error)[:200]}")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"allowed_hosts": ", ".join(ALLOWED_HOSTS)})


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/api/jobs", status_code=202)
def create_job(request: DownloadRequest, background_tasks: BackgroundTasks):
    url = request.url.strip()
    if not is_allowed_url(url):
        domains = ", ".join(ALLOWED_HOSTS)
        raise HTTPException(status_code=400, detail=f"Use a valid URL from: {domains}.")

    cleanup_expired_jobs()
    job_id = uuid.uuid4().hex
    job_directory = DOWNLOAD_ROOT / job_id
    try:
        job_directory.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        raise HTTPException(status_code=500, detail="Could not create the temporary download directory.") from error

    job = {
        "id": job_id,
        "status": "downloading",
        "progress": "Starting",
        "message": "Preparing download…",
        "files": [],
        "songs_completed": 0,
        "songs_total": 1 if request.mode == "single" else None,
        "archive": None,
        "directory": job_directory,
        "created_at": time.time(),
    }
    with jobs_lock:
        jobs[job_id] = job
    background_tasks.add_task(download_job, job_id, url, request.mode, job_directory)
    return public_job(job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="This job has expired or does not exist.")
        return public_job(job)


@app.get("/api/jobs/{job_id}/files/{filename}")
def download_file(job_id: str, filename: str):
    safe_name = Path(filename).name
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None or job["status"] != "complete" or safe_name not in job["files"]:
            raise HTTPException(status_code=404, detail="File not found.")
        file_path = Path(job["directory"]) / safe_name

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File has expired.")
    return FileResponse(file_path, media_type="audio/mpeg", filename=safe_name)


@app.get("/api/jobs/{job_id}/archive")
def download_archive(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None or job["status"] != "complete" or not job["archive"]:
            raise HTTPException(status_code=404, detail="ZIP file not found.")
        archive_path = Path(job["directory"]) / job["archive"]

    if not archive_path.is_file():
        raise HTTPException(status_code=404, detail="ZIP file has expired.")
    return FileResponse(archive_path, media_type="application/zip", filename="music-downloads.zip")
