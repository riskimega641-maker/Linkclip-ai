# LinkClip AI Website MVP

A mobile-friendly web UI + Flask backend for generating short clips from supported public URLs using yt-dlp and FFmpeg. Optional local Whisper transcription can guide highlight selection and subtitles.

## Run locally
Requirements: Docker Desktop or Python 3.11 + FFmpeg + ffprobe.

### Docker (recommended)
```bash
docker compose up --build
```
Open http://localhost:5000

### Python
```bash
pip install -r requirements.txt
# install FFmpeg separately and make sure ffmpeg + ffprobe are in PATH
python app.py
```

## Deploy as a public website
This MVP needs a server that can run Python, FFmpeg, and yt-dlp. For a public service, use a VPS with sufficient CPU/RAM/disk rather than a static-only website host. Install Docker on the VPS, upload this project, and run `docker compose up -d --build`. Then configure a domain and HTTPS reverse proxy (Caddy or Nginx) in front of port 5000.

Recommended starting resources for testing: 2 CPU, 4 GB RAM, and enough disk space for source video + rendered clips. Whisper adds significant memory/storage and may be slow on CPU. The Docker build includes Whisper; `WHISPER_MODEL=tiny` is set to reduce first-run model size. For better Indonesian transcription, try `base` or `small` if the server has enough resources.

## Limitations / security
- Prototype, not production-ready. Before making it public, add authentication, rate limiting, quotas, robust SSRF protection (including DNS rebinding), job queue, job cleanup, storage quotas, monitoring, and HTTPS.
- It does not bypass DRM, paywalls, private/login-only videos, or platform access restrictions. Source extraction may stop working when platforms change.
- Only use videos you own or have permission to process and respect platform terms/copyright.
- Smart selection is heuristic transcript scoring, not a generative model that truly predicts virality.
- Jobs are stored in `jobs/`; cleanup is manual for this MVP.
