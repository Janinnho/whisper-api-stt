# Whisper Web App

A web application for audio transcription using OpenAI's Whisper model. The application supports both local transcription with the Whisper model and the use of the OpenAI Cloud API.

## Features

- Transcription with local Whisper model (tiny, base, small, medium, large)
- Transcription with OpenAI Cloud API (whisper-1, gpt-4o-transcribe, gpt-4o-mini-transcribe)
- OpenAI API-compatible endpoint (`/v1/audio/transcriptions`)
- Optional API key protection for the local API endpoint
- Modern UI with copy-to-clipboard functionality
- Configurable default model for API compatibility mode
- Automatic chunking for large files when using the OpenAI Cloud API: files > 20 MB are split and uploaded in parts; results are merged
 - Optional timestamps (segments) for local Whisper and whisper-1. Note: gpt-4o-transcribe and gpt-4o-mini-transcribe currently do not return timestamps.
 - Provide a YouTube or Twitch URL instead of a file: the app downloads audio via yt-dlp, then transcribes it (temporary file is deleted).

## Installation

### Prerequisites

- Docker (for Docker method)
- Python 3.9+ (for local installation)

### With Docker

```bash
#Download from Github
git clone https://github.com/Janinnho/whisper-api-stt.git
cd whisper-api-stt

# Build the Docker image
docker build -t whisper-web-app .

# Start the container without API key
docker run -p 5000:5000 whisper-web-app

# Start with local API key
docker run -p 5000:5000 -e LOCAL_API_KEY=your_api_key whisper-web-app

# Start with OpenAI API key for cloud transcription
docker run -p 5000:5000 -e OPENAI_API_KEY=your_openai_key whisper-web-app
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

Open http://localhost:5000 in your browser and use the form to upload and transcribe audio files.
Alternatively, switch input source to "YouTube/Twitch URL" and paste a media link; the app will download the audio and transcribe it.

The web interface offers two main tabs:
1. **Transcribe**: Upload and transcribe audio files using either local Whisper models or OpenAI Cloud API.
2. **API Settings**: Configure which local model should be used when the API receives requests with `model=whisper-1`.

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

### Cloud upload limits and chunking

When using the OpenAI Cloud API, files larger than 20 MB are automatically split into smaller segments before upload. The app re-encodes audio to mono, 16 kHz at 64 kbps to create predictable, small chunks and then merges the partial transcriptions.

You can override defaults with environment variables:

- `MAX_CLOUD_FILE_MB` (default: `20`): Threshold above which files are chunked.
- `CHUNK_DURATION_SECONDS` (default: `600` = 10 minutes): Duration per chunk after re-encoding.
- `REENCODE_BITRATE` (default: `64k`): Audio bitrate for chunked files. Lower values yield smaller chunks.

If ffmpeg is not found, the server will return a clear error message indicating it needs to be installed.

### Timestamps support

- Local Whisper models and the OpenAI whisper-1 model can return per-segment timestamps that the UI can display and export.
- The GPT-4o transcription models (gpt-4o-transcribe and gpt-4o-mini-transcribe) currently do not provide timestamped segments in responses. The UI will disable the timestamps option when these models are selected.
