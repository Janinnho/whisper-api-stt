# Whisper Web App

A web application for audio transcription using OpenAI's Whisper model. The application supports both local transcription with the Whisper model and the use of the OpenAI Cloud API.

## Features

- Transcription with local Whisper model (tiny, base, small, medium, large)
- Transcription with OpenAI Cloud API (whisper-1, gpt-4o-transcribe, gpt-4o-mini-transcribe)
- OpenAI API-compatible endpoint (`/v1/audio/transcriptions`)
- Optional API key protection for the local API endpoint
- Modern UI with copy-to-clipboard functionality
- Configurable default model for API compatibility mode

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

## Usage

### Web Interface

Open http://localhost:5000 in your browser and use the form to upload and transcribe audio files.

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
