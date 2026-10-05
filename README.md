# Personal Music Downloader

A small FastAPI web application that creates temporary MP3 downloads from allowed source URLs. It is designed for a personal Render deployment.

## What it includes

- A responsive browser interface for one track or a playlist.
- `yt-dlp` and FFmpeg inside Docker.
- Server-side jobs with browser polling, playlist track counts, and completed-song names.
- One temporary ZIP archive containing every completed MP3; no database or permanent file storage.
- A host allowlist to prevent using the app as a general-purpose server-side fetcher.

## Run locally with Docker

```bash
docker build -t personal-music-downloader .
docker run --rm -p 8000:8000 -e PORT=8000 personal-music-downloader
```

Then visit `http://localhost:8000`.

## Deploy on Render

1. Create a new GitHub repository and upload the contents of this `deployment` folder to its root.
2. In Render, select **New > Blueprint** and connect that GitHub repository. Render reads `render.yaml`.
3. Deploy. The health check is `/health`; the site will receive an `onrender.com` address.
4. Keep the default `ALLOWED_HOSTS` value for YouTube, or set it to a comma-separated list of approved domains in Render's environment settings.

Render Free is suitable for personal testing. It sleeps when idle and its filesystem is temporary, so completed downloads must be saved immediately by the browser. Jobs and links vanish on restart or redeploy.

## Limits and responsible use

This project is intended for material you own or are authorized to download. Do not make it an unrestricted public service. Before exposing it to other people, add authentication, rate limits, abuse controls, and durable job/file storage.
