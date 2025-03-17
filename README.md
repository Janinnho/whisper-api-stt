# Whisper Web App

A web application for audio transcription using OpenAI's Whisper model. The application supports both local transcription with the Whisper model and the use of the OpenAI Cloud API.

## Features

- Transcription with local Whisper model
- Transcription with OpenAI Cloud API (optional)
- OpenAI API-compatible endpoint (`/v1/audio/transcriptions`)
- Optional API key protection for the local API endpoint

## Installation

### Prerequisites

- Docker

### With Docker

```bash
# Build the Docker image
docker build -t whisper-web-app .

# Start the container without API key
docker run -p 5000:5000 whisper-web-app

# Start with local API key
docker run -p 5000:5000 -e LOCAL_API_KEY=your_api_key whisper-web-app

# Start with OpenAI API key for cloud transcription
docker run -p 5000:5000 -e OPENAI_API_KEY=your_openai_key whisper-web-app
```

## Usage

### Web Interface

Open http://localhost:5000 in your browser and use the form to upload and transcribe audio files.

### API Usage

The app provides an OpenAI-compatible API endpoint:

```bash
# Transcription without API key (if none is configured)
curl -X POST -F "file=@audio.mp3" -F "model=base" http://localhost:5000/v1/audio/transcriptions

# Transcription with API key (if configured)
curl -X POST -H "Authorization: Bearer your_api_key" -F "file=@audio.mp3" -F "model=base" http://localhost:5000/v1/audio/transcriptions
```

Model options: tiny, base, small, medium, large (the larger the model, the more accurate but slower)

## Environment Variables

- `OPENAI_API_KEY`: API key for OpenAI Cloud (optional)
- `LOCAL_API_KEY`: API key to protect the local API endpoint (optional)