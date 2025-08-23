# Whisper Web App

A web app for audio transcription using local Whisper and OpenAI Cloud. Now with background jobs, Jobs/History views, database persistence, Cloudflare Access auth support, and media URL (YouTube/Twitch) ingestion.

## Features

- Local Whisper models (tiny, base, small, medium, large)
- OpenAI Cloud API (whisper-1, gpt-4o-transcribe, gpt-4o-mini-transcribe)
- Background jobs that keep running even if the page is closed
- Jobs view: monitor progress and cancel running transcriptions
- History: browse previous transcriptions, view details, download or delete
- Database persistence (SQLite); Docker image exposes a /data volume for the DB
- Cloudflare Access support via header CF-Access-Authenticated-User-Email when enabled
- OpenAI API-compatible endpoint (`/v1/audio/transcriptions`) with optional LOCAL_API_KEY check
- YouTube/Twitch URL ingestion via yt-dlp, with progress and temporary cleanup
- Modern UI with copy, search and export

## Installation

### Prerequisites

- Docker (for Docker method)
- Python 3.9+ (for local installation)

### With Docker (recommended)

```bash
#Download from Github
git clone https://github.com/Janinnho/whisper-api-stt.git
cd whisper-api-stt

# Build the Docker image
docker build -t whisper-web-app .

# Start the container without API key
docker run -p 5000:5000 -v whisper-data:/data whisper-web-app

# Start with local API key
docker run -p 5000:5000 -v whisper-data:/data -e LOCAL_API_KEY=your_api_key whisper-web-app

# Start with OpenAI API key for cloud transcription
docker run -p 5000:5000 -v whisper-data:/data -e OPENAI_API_KEY=your_openai_key whisper-web-app

# Enable Cloudflare Access header enforcement (only allow authenticated users)
docker run -p 5000:5000 -v whisper-data:/data -e CF_ACCESS_ENFORCE=true whisper-web-app
```

Note: The image installs ffmpeg which is required for automatic chunking.
It also includes yt-dlp to support YouTube/Twitch URL downloads.

### With Python Virtual Environment

```bash
#Download from Github
git clone https://github.com/Janinnho/whisper-api-stt.git
cd whisper-api-stt

# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On Windows
venv\Scripts\activate
# On macOS/Linux
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the Flask application
# Without API keys
export DB_PATH=$(pwd)/data/app.db
mkdir -p $(dirname "$DB_PATH")
flask run
# or
python app.py

# With API keys
export LOCAL_API_KEY=your_api_key  # For local API protection
export OPENAI_API_KEY=your_openai_key  # For OpenAI cloud transcription
flask run
```

Note: For cloud transcription with automatic chunking, ffmpeg needs to be installed locally. On macOS:

```bash
brew install ffmpeg
# URL downloads require yt-dlp as well
pip install yt-dlp
```

On Ubuntu/Debian:

```bash
sudo apt-get update && sudo apt-get install -y ffmpeg
# URL downloads require yt-dlp as well
pip install yt-dlp
```

## Usage

### Web Interface

Open http://localhost:5000 in your browser.

Tabs:
- Transcribe: file upload or YouTube/Twitch URL; choose local or cloud model; optional timestamps
- Jobs: shows running jobs with live progress; cancel jobs
- History: list completed/error/cancelled transcriptions; view details and download text; delete entries
- API Settings: pick default local model to serve when clients pass `model=whisper-1`
 - Users (only when `CF_ACCESS_ENFORCE=true`): list authenticated users; first user becomes admin automatically; admins can toggle admin state of users

### API Usage

The app provides an OpenAI-compatible API endpoint:

```bash
# Using local Whisper models directly
curl -X POST -F "file=@audio.mp3" -F "model=base" http://localhost:5000/v1/audio/transcriptions

# Using OpenAI compatibility mode (will use the model configured in the API Settings)
curl -X POST -F "file=@audio.mp3" -F "model=whisper-1" http://localhost:5000/v1/audio/transcriptions

# With API key (if configured)
curl -X POST -H "Authorization: Bearer your_api_key" -F "file=@audio.mp3" -F "model=base" http://localhost:5000/v1/audio/transcriptions
```

Available local model options:
- `tiny`: Fastest, least accurate
- `base`: Good balance between speed and accuracy
- `small`: Better accuracy, slower than base
- `medium`: High accuracy, slower
- `large`: Highest accuracy, slowest

The API can also accept `whisper-1` as a model parameter for compatibility with applications that support OpenAI's API. In this case, the app will use the model configured in the API Settings tab.

## Environment Variables

- `OPENAI_API_KEY`: API key for OpenAI Cloud (optional)
- `LOCAL_API_KEY`: API key to protect the local API endpoint (optional)
- `CF_ACCESS_ENFORCE`: if `true`, requires Cloudflare Access header `CF-Access-Authenticated-User-Email` for the web UI and related endpoints. The OpenAI-compatible API endpoint `/v1/audio/transcriptions` remains accessible without this header (still optionally guarded by `LOCAL_API_KEY`).

When Cloudflare enforcement is enabled:
- The first authenticated user becomes admin automatically.
- Admins see all jobs/history and can toggle admin rights for any user.
- Non-admin users only see their own jobs/history.
- `DB_PATH`: path to SQLite database file (defaults to `/data/app.db`); ensure the directory exists or map a Docker volume

### Cloud upload limits and chunking

When using the OpenAI Cloud API, files larger than 20 MB are automatically split into smaller segments before upload. The app re-encodes audio to mono, 16 kHz at 64 kbps to create predictable, small chunks and then merges the partial transcriptions.

You can override defaults with environment variables:

- `MAX_CLOUD_FILE_MB` (default: `20`): Threshold above which files are chunked.
- `CHUNK_DURATION_SECONDS` (default: `600` = 10 minutes): Duration per chunk after re-encoding.
- `REENCODE_BITRATE` (default: `64k`): Audio bitrate for chunked files. Lower values yield smaller chunks.

If ffmpeg is not found, the server will return a clear error message indicating it needs to be installed.

## Data persistence and Docker volumes

By default, the database is created at `/data/app.db`. The Docker image declares `/data` as a volume. Use `-v whisper-data:/data` (named volume) or `-v $(pwd)/data:/data` to persist the DB between restarts.

Records include job metadata, status, timing, the transcribed text and (if requested/available) segments. You can delete records from the History tab.

### Timestamps support

- Local Whisper models and the OpenAI whisper-1 model can return per-segment timestamps that the UI can display and export.
- The GPT-4o transcription models (gpt-4o-transcribe and gpt-4o-mini-transcribe) currently do not provide timestamped segments in responses. The UI will disable the timestamps option when these models are selected.
