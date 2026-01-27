# Whisper Web App

A web app for audio transcription using local Whisper and OpenAI Cloud. Features include an Admin Console, multi-method authentication, background jobs, database persistence, and media URL (YouTube/Twitch) ingestion.

## Features

- **Local Whisper models**: tiny, base, small, medium, large
- **OpenAI Cloud API**: whisper-1, gpt-4o-transcribe, gpt-4o-mini-transcribe
- **Admin Console**: Comprehensive web-based administration
  - Settings management (authentication, API configuration)
  - User management (create, edit, promote/demote)
  - API key management (create, enable/disable, track usage)
  - Global job monitoring for administrators
- **Multi-Method Authentication**:
  - Local authentication (email/password)
  - HTTP header authentication (Cloudflare Access, Authelia, etc.)
  - OpenID Connect (Microsoft Entra ID, Okta, etc.)
- **Background jobs**: Continue running even if the browser is closed
- **Jobs view**: Monitor progress and cancel running transcriptions
- **History**: Browse, view, download, and delete previous transcriptions
- **Database persistence**: SQLite with Docker volume support
- **YouTube/Twitch URL ingestion**: via yt-dlp with progress tracking
- **Internationalization**: English and German (auto-detected from browser)
- **Theme support**: Light/Dark/Auto modes with user preference persistence
- **OpenAI-compatible API**: `/v1/audio/transcriptions` endpoint

## Installation

### Prerequisites

- Docker (for Docker method)
- Python 3.9+ (for local installation)

### With Docker (recommended)

```bash
# Download from Github
git clone https://github.com/Janinnho/whisper-api-stt.git
cd whisper-api-stt

# Build the Docker image
docker build -t whisper-web-app .

# Start the container (minimal configuration)
docker run -p 5001:5001 -v whisper-data:/data whisper-web-app

# Start with OpenAI API key for cloud transcription
docker run -p 5001:5001 -v whisper-data:/data -e OPENAI_API_KEY=your_openai_key whisper-web-app
```

Note: The image includes ffmpeg (required for automatic chunking) and yt-dlp (for YouTube/Twitch URL downloads).

### With Python Virtual Environment

```bash
# Download from Github
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
export DB_PATH=$(pwd)/data/app.db
mkdir -p $(dirname "$DB_PATH")

# Without OpenAI API key (local models only)
flask run --port 5001
# or
python app.py

# With OpenAI API key
export OPENAI_API_KEY=your_openai_key
flask run --port 5001
```

Note: For cloud transcription with automatic chunking, ffmpeg needs to be installed locally.

On macOS:
```bash
brew install ffmpeg
```

On Ubuntu/Debian:
```bash
sudo apt-get update && sudo apt-get install -y ffmpeg
```

## First-Time Setup

1. Open http://localhost:5001 in your browser
2. Complete the Setup Wizard to create your admin account
3. Access the Admin Console to configure:
   - Authentication methods
   - API keys
   - Other settings

## Usage

### Web Interface

Open http://localhost:5001 in your browser.

**Tabs:**
- **Transcribe**: File upload or YouTube/Twitch URL; choose local or cloud model; optional timestamps
- **Jobs**: Shows running jobs with live progress; cancel jobs
- **History**: List completed/error/cancelled transcriptions; view details and download text; delete entries
- **Settings**: Configure default API model
- **Admin** (admins only): Full system administration

### Admin Console

Accessible at `/admin` for admin users. Features:
- **Dashboard**: System statistics overview
- **Settings**: Authentication configuration, API settings
- **Users**: Manage local users, promote/demote admins
- **API Keys**: Create and manage API keys for the transcription endpoint
- **Jobs**: View and manage all jobs across all users

### API Usage

The app provides an OpenAI-compatible API endpoint:

```bash
# Using local Whisper models directly
curl -X POST -F "file=@audio.mp3" -F "model=base" http://localhost:5001/v1/audio/transcriptions

# Using OpenAI compatibility mode (uses configured default model)
curl -X POST -F "file=@audio.mp3" -F "model=whisper-1" http://localhost:5001/v1/audio/transcriptions

# With API key (if required in Admin Console)
curl -X POST -H "Authorization: Bearer your_api_key" -F "file=@audio.mp3" -F "model=base" http://localhost:5001/v1/audio/transcriptions
```

Available local model options:
- `tiny`: Fastest, least accurate
- `base`: Good balance between speed and accuracy
- `small`: Better accuracy, slower than base
- `medium`: High accuracy, slower
- `large`: Highest accuracy, slowest

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Optional | API key for OpenAI Cloud transcription |
| `DB_PATH` | Optional | Path to SQLite database (default: `/data/app.db`) |

**Note**: All other configuration (authentication, API keys, etc.) is managed through the Admin Console and stored in the database.

## Authentication Methods

### Local Authentication
- Email and password stored securely with bcrypt hashing
- Managed through Admin Console

### HTTP Header Authentication
- Supports headers like `CF-Access-Authenticated-User-Email`
- Configurable header name in Admin Console
- Works with Cloudflare Access, Authelia, and similar solutions

### OpenID Connect (OIDC)
- Configure via Admin Console with:
  - Client ID
  - Client Secret
  - Discovery URL (e.g., `https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration`)
- Works with Microsoft Entra ID, Okta, Auth0, etc.

## Data Persistence

By default, the database is created at `/data/app.db`. The Docker image declares `/data` as a volume.

```bash
# Named volume (recommended)
docker run -p 5001:5001 -v whisper-data:/data whisper-web-app

# Bind mount
docker run -p 5001:5001 -v $(pwd)/data:/data whisper-web-app
```

Records include:
- Job metadata, status, timing
- Transcribed text and segments (if timestamps enabled)
- User accounts and preferences
- Settings and API keys

## Cloud Upload Limits and Chunking

When using the OpenAI Cloud API, files larger than 20 MB are automatically split into smaller segments. The app re-encodes audio to mono, 16 kHz at 64 kbps and merges partial transcriptions.

Default settings (configurable in Admin Console):
- `max_cloud_file_mb`: 20
- `chunk_duration_seconds`: 600 (10 minutes)
- `reencode_bitrate`: 64k

## Timestamps Support

- Local Whisper models and whisper-1 can return per-segment timestamps
- GPT-4o transcription models don't provide timestamped segments
- The UI disables timestamp option for unsupported models

## Migration from v1.2

If upgrading from v1.2:

1. The port has changed from 5000 to 5001
2. `LOCAL_API_KEY` environment variable is deprecated - use Admin Console API keys instead
3. `CF_ACCESS_ENFORCE` environment variable is deprecated - configure authentication in Admin Console
4. Run the Setup Wizard on first access to create an admin account

Your existing database will be automatically migrated to the new schema.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history and changes.
