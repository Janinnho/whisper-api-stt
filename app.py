import os
import tempfile
import json
import subprocess
import mimetypes
from typing import List, Callable, Optional
import threading
import uuid
import shutil
import time
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response, g
import requests
import whisper
from urllib.parse import urlparse
import yt_dlp
from sqlalchemy import create_engine, Column, String, Integer, Text, DateTime, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base

from flask_cors import CORS
app = Flask(__name__)
# Enable CORS for all routes to allow external clients (e.g., Dictate) to communicate with this API
CORS(app)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LOCAL_API_KEY = os.getenv("LOCAL_API_KEY", None)  # Optional API key for local API
DEFAULT_API_MODEL = "base"  # Default model for API requests

# Database setup (SQLite by default; path configurable)
DB_PATH = os.getenv("DB_PATH", "/data/app.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

# Cloudflare Access enforcement
CF_ACCESS_ENFORCE = os.getenv("CF_ACCESS_ENFORCE", "false").strip().lower() in ("1", "true", "yes", "on")

# Cloud Upload Limits / Chunking Settings
# Threshold in MB after which we split before sending to OpenAI API
MAX_CLOUD_FILE_MB = float(os.getenv("MAX_CLOUD_FILE_MB", "20"))
# Duration per chunk in seconds when splitting (kept small to stay well below size limits)
CHUNK_DURATION_SECONDS = int(os.getenv("CHUNK_DURATION_SECONDS", "600"))  # 10 minutes
# Re-encode audio when chunking to ensure predictable small chunk sizes
REENCODE_BITRATE = os.getenv("REENCODE_BITRATE", "64k")  # audio bitrate

if not OPENAI_API_KEY:
    print("Note: No OPENAI_API_KEY found. Cloud transcription will not be available.")

# Load local Whisper model (will be downloaded on first call)
local_model = None
current_model_size = None
JOBS_LOCK = threading.Lock()
JOBS = {}


class TranscriptionJob(Base):
    __tablename__ = "transcription_jobs"
    id = Column(String, primary_key=True)
    user_email = Column(String, nullable=True)
    source_type = Column(String, nullable=True)  # file|url
    source_url = Column(Text, nullable=True)
    original_filename = Column(String, nullable=True)
    method = Column(String, nullable=False)  # local|cloud
    local_model_size = Column(String, nullable=True)
    cloud_model = Column(String, nullable=True)
    with_timestamps = Column(Boolean, default=False)
    status = Column(String, default="queued")  # queued|running|completed|error|cancelled
    percent = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    result_text = Column(Text, nullable=True)
    result_segments = Column(Text, nullable=True)  # JSON string
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)


class User(Base):
    __tablename__ = "users"
    email = Column(String, primary_key=True)
    is_admin = Column(Boolean, default=False)
    first_seen = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, nullable=True)


def init_db():
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    except Exception:
        pass
    Base.metadata.create_all(engine)


@app.before_request
def _cf_access_auth():
    # Set user context; enforce Cloudflare Access header for UI routes only (API remains unaffected)
    g.user_email = None
    hdr = request.headers.get("CF-Access-Authenticated-User-Email")
    if hdr:
        g.user_email = hdr

    path = request.path or ""

    # If CF auth is enforced, require header for UI and non-API routes
    if CF_ACCESS_ENFORCE:
        if not path.startswith("/v1/audio/transcriptions"):
            if not g.user_email:
                return jsonify({"error": "Unauthorized (Cloudflare Access required)."}), 401

    # Persist or update user when CF is used and header present
    if g.user_email:
        session = SessionLocal()
        user = session.get(User, g.user_email)
        now = datetime.utcnow()
        if not user:
            # Create and maybe make first admin
            user = User(email=g.user_email, is_admin=False, first_seen=now, last_seen=now)
            session.add(user)
            session.flush()
            # If no admin exists, make this one admin
            any_admin = session.query(User).filter(User.is_admin == True).first()
            if not any_admin:
                user.is_admin = True
        else:
            user.last_seen = now
        session.commit()
        session.close()

def load_local_model(model_size="base"):
    global local_model, current_model_size
    if local_model is None or current_model_size != model_size:
        print(f"Loading local Whisper model ({model_size})...")
        local_model = whisper.load_model(model_size)
        current_model_size = model_size
    return local_model

def transcribe_with_local_model(audio_file, model_size="base", progress: Optional[Callable[[str, int], None]] = None, return_segments: bool = False):
    # Wrapper für FileStorage -> Pfad
    with tempfile.NamedTemporaryFile(delete=True) as temp_file:
        audio_file.save(temp_file.name)
        return transcribe_local_from_path(temp_file.name, model_size, progress, return_segments)


def transcribe_local_from_path(file_path: str, model_size="base", progress: Optional[Callable[[str, int], None]] = None, return_segments: bool = False, cancel_check: Optional[Callable[[], bool]] = None):
    if progress:
        progress("Lade lokales Whisper-Modell", 5)
    model = load_local_model(model_size)
    if progress:
        progress("Transkribiere lokal…", 10)
    # For better cancellation responsiveness on large files, split into chunks if duration is long
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", file_path
        ], text=True)
        duration = float(out.strip())
    except Exception:
        duration = 0.0

    if duration and duration > CHUNK_DURATION_SECONDS * 1.5:
        # Chunked local transcription with cancellation between chunks
        tmpdir = tempfile.mkdtemp(prefix="local_chunk_")
        try:
            out_pattern = os.path.join(tmpdir, "chunk_%03d.mp3")
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", file_path,
                "-ac", "1", "-ar", "16000", "-b:a", REENCODE_BITRATE,
                "-f", "segment", "-segment_time", str(CHUNK_DURATION_SECONDS),
                "-reset_timestamps", "1",
                out_pattern,
            ]
            subprocess.run(cmd, check=True)
        except Exception as e:
            # Fallback to single-shot
            result = model.transcribe(file_path)
            if progress:
                progress("Fertig", 100)
            text = result.get("text", "")
            if not return_segments:
                return text
            segments = [
                {"start": float(s.get("start", 0.0)), "end": float(s.get("end", 0.0)), "text": s.get("text", "")}
                for s in (result.get("segments") or [])
            ]
            return {"text": text, "segments": segments}

        chunks: List[str] = sorted(
            [os.path.join(tmpdir, f) for f in os.listdir(tmpdir) if f.startswith("chunk_") and f.endswith(".mp3")]
        )
        parts: List[str] = []
        all_segments: List[dict] = []
        total = len(chunks) or 1
        cum_offset = 0.0
        # gather durations
        durations = []
        for p in chunks:
            try:
                out = subprocess.check_output([
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=nw=1:nk=1", p
                ], text=True)
                durations.append(float(out.strip()))
            except Exception:
                durations.append(float(CHUNK_DURATION_SECONDS))

        for idx, ch in enumerate(chunks, start=1):
            if cancel_check and cancel_check():
                if progress:
                    progress("Abgebrochen", 100)
                shutil.rmtree(tmpdir, ignore_errors=True)
                raise RuntimeError("Cancelled")
            if progress:
                percent = 10 + int(85 * (idx - 1) / total)
                progress(f"Transkribiere Chunk {idx}/{total}", percent)
            r = model.transcribe(ch)
            parts.append((r.get("text") or "").strip())
            if return_segments:
                for s in (r.get("segments") or []):
                    all_segments.append({
                        "start": float(s.get("start", 0.0)) + cum_offset,
                        "end": float(s.get("end", 0.0)) + cum_offset,
                        "text": s.get("text", "")
                    })
            try:
                cum_offset += float(durations[idx-1] or 0.0)
            except Exception:
                cum_offset += float(CHUNK_DURATION_SECONDS)
        merged = "\n\n".join([p for p in parts if p])
        if progress:
            progress("Fertig", 100)
        if not return_segments:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return merged
        shutil.rmtree(tmpdir, ignore_errors=True)
        return {"text": merged, "segments": all_segments or None}

    # Small files: single-shot
    result = model.transcribe(file_path)
    if progress:
        progress("Fertig", 100)
    text = result.get("text", "")
    if not return_segments:
        return text
    segments = [
        {"start": float(s.get("start", 0.0)), "end": float(s.get("end", 0.0)), "text": s.get("text", "")}
        for s in (result.get("segments") or [])
    ]
    return {"text": text, "segments": segments}

def transcribe_with_openai_api(audio_file, model="whisper-1", progress: Optional[Callable[[str, int], None]] = None, return_segments: bool = False):
    valid_models = ["whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"]
    if model not in valid_models:
        model = "whisper-1"  # Default to whisper-1 if invalid model
    # Save uploaded file and delegate to path-based function
    with tempfile.NamedTemporaryDirectory() as tmpdir:
        orig_ext = os.path.splitext(audio_file.filename or "")[1] or ".bin"
        input_path = os.path.join(tmpdir, f"input{orig_ext}")
        audio_file.save(input_path)
        return transcribe_with_openai_api_path(input_path, model, progress, return_segments)


def transcribe_with_openai_api_path(input_path: str, model="whisper-1", progress: Optional[Callable[[str, int], None]] = None, return_segments: bool = False, cancel_check: Optional[Callable[[], bool]] = None):
    valid_models = ["whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"]
    if model not in valid_models:
        model = "whisper-1"

    file_size_bytes = os.path.getsize(input_path)
    size_mb = file_size_bytes / (1024 * 1024)

    def _send_to_openai(file_path: str, override_content_type: str = None, want_verbose: bool = False) -> dict:
        ct = override_content_type or (mimetypes.guess_type(file_path)[0] or "application/octet-stream")
        if progress:
            progress(f"Sende an OpenAI: {os.path.basename(file_path)}", 0)
        # Build form data as list of tuples to support repeated fields
        data_fields = [("model", model)]
        if want_verbose:
            data_fields.append(("response_format", "verbose_json"))
            # Newer GPT-4o transcription models require explicit timestamp granularity
            if model.startswith("gpt-4o"):
                # Request both word and segment to maximize compatibility
                data_fields.append(("timestamp_granularities[]", "segment"))
                data_fields.append(("timestamp_granularities[]", "word"))
        with open(file_path, "rb") as f:
            response = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                files={"file": (os.path.basename(file_path), f, ct)},
                data=data_fields
            )
        if response.status_code == 200:
            try:
                return response.json()
            except Exception:
                return {"__error__": "Invalid JSON"}
        # Retry without verbose if requested and failed
        if want_verbose:
            with open(file_path, "rb") as f2:
                resp2 = requests.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    files={"file": (os.path.basename(file_path), f2, ct)},
                    data=[("model", model)]
                )
            if resp2.status_code == 200:
                return {"text": resp2.json().get("text", "")}
            return {"__error__": f"{resp2.status_code}: {resp2.text}"}
        return {"__error__": f"{response.status_code}: {response.text}"}

    def _extract_segments(api_resp: dict):
        if not isinstance(api_resp, dict):
            return None
        # 1) Whisper-style verbose_json
        segs = api_resp.get("segments")
        if isinstance(segs, list) and segs:
            out = []
            for s in segs:
                try:
                    out.append({
                        "start": float(s.get("start", 0.0)),
                        "end": float(s.get("end", 0.0)),
                        "text": s.get("text", "")
                    })
                except Exception:
                    continue
            return out or None
        # 2) Some GPT-4o responses may include a 'timestamps' top-level list
        ts = api_resp.get("timestamps")
        if isinstance(ts, list) and ts:
            out = []
            for s in ts:
                try:
                    out.append({
                        "start": float(s.get("start", 0.0)),
                        "end": float(s.get("end", 0.0)),
                        "text": s.get("text", "")
                    })
                except Exception:
                    continue
            return out or None
        # 3) Nested structure: { timestamps: { segments: [...] } }
        tss = api_resp.get("timestamps")
        if isinstance(tss, dict):
            segs2 = tss.get("segments")
            if isinstance(segs2, list) and segs2:
                out = []
                for s in segs2:
                    try:
                        out.append({
                            "start": float(s.get("start", 0.0)),
                            "end": float(s.get("end", 0.0)),
                            "text": s.get("text", "")
                        })
                    except Exception:
                        continue
                return out or None
        # 4) Fallback: word-level timestamps only -> map words to pseudo-segments per word
        words = api_resp.get("words")
        if isinstance(words, list) and words:
            out = []
            for w in words:
                try:
                    out.append({
                        "start": float(w.get("start", 0.0)),
                        "end": float(w.get("end", 0.0)),
                        "text": w.get("word", w.get("text", ""))
                    })
                except Exception:
                    continue
            return out or None
        return None

    def _ffprobe_duration_seconds(file_path: str) -> float:
        try:
            out = subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", file_path
            ], text=True)
            return float(out.strip())
        except Exception:
            return 0.0

    # Small file: direct upload
    if size_mb <= MAX_CLOUD_FILE_MB:
        if progress:
            progress("Direkt-Upload zu OpenAI", 10)
        resp = _send_to_openai(input_path, want_verbose=return_segments)
        if "__error__" in resp:
            return f"Transcription error: {resp['__error__']}"
        if progress:
            progress("Fertig", 100)
        text = resp.get("text", "")
        if not return_segments:
            return text or "No transcription found."
        segments = _extract_segments(resp)
        return {"text": text or "No transcription found.", "segments": segments}

    # Large file: split into chunks and transcribe sequentially
    if progress:
        progress("Datei zu groß – segmentiere Audio…", 5)
    tmpdir = tempfile.mkdtemp(prefix="chunk_")
    try:
        out_pattern = os.path.join(tmpdir, "chunk_%03d.mp3")
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", input_path,
            "-ac", "1", "-ar", "16000", "-b:a", REENCODE_BITRATE,
            "-f", "segment", "-segment_time", str(CHUNK_DURATION_SECONDS),
            "-reset_timestamps", "1",
            out_pattern,
        ]
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        return (
            "Transcription error: ffmpeg not found on system. "
            "Install ffmpeg (e.g., brew install ffmpeg) or use the provided Docker image."
        )
    except subprocess.CalledProcessError as e:
        return f"Transcription error during audio chunking: {str(e)}"

    chunks: List[str] = sorted(
        [os.path.join(tmpdir, f) for f in os.listdir(tmpdir) if f.startswith("chunk_") and f.endswith(".mp3")]
    )
    if not chunks:
        return "Transcription error: No chunks were produced during splitting."

    parts: List[str] = []
    all_segments: List[dict] = []
    cumulative_offset = 0.0
    chunk_durations = [
        _ffprobe_duration_seconds(p) for p in chunks
    ] if return_segments else None
    total = len(chunks)
    for idx, chunk_path in enumerate(chunks, start=1):
        if cancel_check and cancel_check():
            return "Cancelled"
        if progress:
            percent = 10 + int(85 * (idx - 1) / total)  # 10-95% über Chunks
            progress(f"Transkribiere Chunk {idx}/{total}", percent)
        resp = _send_to_openai(chunk_path, override_content_type="audio/mpeg", want_verbose=return_segments)
        if "__error__" in resp:
            return f"Transcription error on chunk {idx}/{total}: {resp['__error__']}"
        chunk_text = resp.get("text", "")
        parts.append(chunk_text.strip())
        if return_segments:
            for s in (_extract_segments(resp) or []):
                start = float(s.get("start", 0.0)) + cumulative_offset
                end = float(s.get("end", 0.0)) + cumulative_offset
                all_segments.append({"start": start, "end": end, "text": s.get("text", "")})
            if chunk_durations and len(chunk_durations) >= idx:
                cumulative_offset += float(chunk_durations[idx-1] or 0.0)
            else:
                cumulative_offset += float(CHUNK_DURATION_SECONDS)

    merged = "\n\n".join([p for p in parts if p])
    if progress:
        progress("Fertig", 100)
    # Clean up chunk dir
    shutil.rmtree(tmpdir, ignore_errors=True)
    if not return_segments:
        return merged or "No transcription found."
    return {"text": merged or "No transcription found.", "segments": all_segments or None}


def _is_supported_media_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.netloc or "").lower()
        allowed_hosts = (
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "youtu.be",
            "www.youtu.be",
            "twitch.tv",
            "www.twitch.tv",
        )
        return any(host.endswith(h) for h in allowed_hosts)
    except Exception:
        return False


def download_audio_from_url(media_url: str, dest_dir: str, progress: Optional[Callable[[str, int], None]] = None) -> str:
    """
    Download audio from a supported media URL (YouTube/Twitch) using yt-dlp.
    Returns the path to the extracted audio file (mp3).
    """
    if not _is_supported_media_url(media_url):
        raise ValueError("Only YouTube or Twitch URLs are supported.")

    os.makedirs(dest_dir, exist_ok=True)
    # We use a fixed basename so we can predict the postprocessed output.
    outtmpl = os.path.join(dest_dir, "download.%(ext)s")
    last_percent = 0

    def hook(d):
        nonlocal last_percent
        try:
            if d.get('status') == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes') or 0
                pct = 0
                if total:
                    pct = int(downloaded / total * 100)
                # Map 0-100 download to 0-30 overall
                mapped = min(30, max(0, int(pct * 0.30)))
                if mapped != last_percent:
                    last_percent = mapped
                    if progress:
                        progress(f"Lade Audio… {pct}%", mapped)
            elif d.get('status') == 'finished':
                if progress:
                    progress("Download abgeschlossen – extrahiere Audio…", 30)
        except Exception:
            pass

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': outtmpl,
        'noplaylist': True,
        'progress_hooks': [hook],
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        # Reduce noisy output
        'quiet': True,
        'no_warnings': True,
    }

    # Run download
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([media_url])
    except Exception as e:
        raise RuntimeError(f"Download failed: {str(e)}")

    # After postprocessing, expected file is download.mp3 in dest_dir
    final_path = os.path.join(dest_dir, "download.mp3")
    if not os.path.isfile(final_path):
        # Fallback: pick the newest audio file in dest_dir
        candidates = [
            os.path.join(dest_dir, f) for f in os.listdir(dest_dir)
            if f.lower().endswith(('.mp3', '.m4a', '.aac', '.wav', '.ogg', '.webm'))
        ]
        if not candidates:
            raise RuntimeError("Audio extraction failed – no audio file produced.")
        final_path = max(candidates, key=lambda p: os.path.getmtime(p))
    return final_path


def _update_job(job_id: str, status: str = None, percent: int = None, result: str = None, error: str = None):
    with JOBS_LOCK:
        job = JOBS.get(job_id, {})
        if status is not None:
            job["status"] = status
        if percent is not None:
            job["percent"] = max(0, min(100, int(percent)))
        if result is not None:
            job["result"] = result
        if error is not None:
            job["error"] = error
        JOBS[job_id] = job

def _job_set_cancel(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return False
        job["cancelled"] = True
        JOBS[job_id] = job
        return True


def _make_progress_cb(job_id: str) -> Callable[[str, int], None]:
    def cb(status: str, percent: int):
        _update_job(job_id, status=status, percent=percent)
    return cb

@app.route("/", methods=["GET", "POST"])
def index():
    transcription = None
    transcription_method = "local"  # Default method is local
    local_model_size = "base"       # Default local model
    cloud_model = "whisper-1"       # Default cloud model
    cloud_available = OPENAI_API_KEY is not None
    
    if request.method == "POST":
        transcription_method = request.form.get("transcription_method", "local")
        local_model_size = request.form.get("local_model_size", "base")
        cloud_model = request.form.get("cloud_model", "whisper-1")
        
        if request.form.get("action") == "save_settings":
            # Save API model setting
            global DEFAULT_API_MODEL
            DEFAULT_API_MODEL = request.form.get("api_model", "base")
            return render_template(
                "index.html", 
                transcription=transcription, 
                selected_method=transcription_method,
                local_model_size=local_model_size,
                cloud_model=cloud_model,
                api_model=DEFAULT_API_MODEL,
                cloud_available=cloud_available,
                settings_saved=True
            )
        
        media_url = (request.form.get("media_url") or "").strip()
        file = request.files.get("audio_file")
        if (not file or file.filename == "") and not media_url:
            transcription = "No file or URL provided!"
        else:
            try:
                # Prepare a temp path from either file upload or URL download
                with tempfile.TemporaryDirectory() as temp_dir:
                    input_path = None
                    if media_url:
                        input_path = download_audio_from_url(media_url, temp_dir)
                    elif file and file.filename:
                        input_path = os.path.join(temp_dir, file.filename or "audio")
                        file.save(input_path)
                    if not input_path or not os.path.exists(input_path):
                        raise RuntimeError("No input audio available")

                    if transcription_method == "local":
                        result = transcribe_local_from_path(input_path, local_model_size, return_segments=False)
                    else:  # cloud
                        if not OPENAI_API_KEY:
                            transcription = "Error: No API key configured for cloud transcription!"
                        else:
                            result = transcribe_with_openai_api_path(input_path, cloud_model, return_segments=False)
                    transcription = result if isinstance(result, str) else result.get("text", "")
            except Exception as e:
                transcription = f"Transcription error: {str(e)}"
    
    return render_template(
        "index.html", 
        transcription=transcription, 
        selected_method=transcription_method,
        local_model_size=local_model_size,
        cloud_model=cloud_model,
        api_model=DEFAULT_API_MODEL,
        cloud_available=cloud_available,
        cf_enforced=CF_ACCESS_ENFORCE,
        current_user_email=getattr(g, "user_email", None),
        user_is_admin=_is_current_user_admin()
    )
    
@app.route("/transcribe", methods=["POST"])
def transcribe_ajax():
    transcription = None
    transcription_method = request.form.get("transcription_method", "local")
    local_model_size = request.form.get("local_model_size", "base")
    cloud_model = request.form.get("cloud_model", "whisper-1")
    with_timestamps = request.form.get("with_timestamps") in ("on", "true", "1")

    media_url = (request.form.get("media_url") or "").strip()
    file = request.files.get("audio_file")
    if (not file or file.filename == "") and not media_url:
        return jsonify({"error": "No file or URL provided!"}), 400

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = None
            if media_url:
                input_path = download_audio_from_url(media_url, temp_dir)
            elif file and file.filename:
                input_path = os.path.join(temp_dir, file.filename or "audio")
                file.save(input_path)
            if not input_path or not os.path.exists(input_path):
                return jsonify({"error": "No input audio available"}), 400

            if transcription_method == "local":
                result = transcribe_local_from_path(input_path, local_model_size, return_segments=with_timestamps)
            else:
                if not OPENAI_API_KEY:
                    return jsonify({"error": "Error: No API key configured for cloud transcription!"}), 400
                # gpt-4o(-mini)-transcribe liefert derzeit keine Timestamps -> ignorieren
                if cloud_model.startswith("gpt-4o"):
                    with_timestamps = False
                result = transcribe_with_openai_api_path(input_path, cloud_model, return_segments=with_timestamps)
    except Exception as e:
        return jsonify({"error": f"Transcription error: {str(e)}"}), 500
    if isinstance(result, dict):
        return jsonify({"transcription": result.get("text", ""), "segments": result.get("segments")})
    return jsonify({"transcription": str(result), "segments": None})


# Async job-based transcription start (for progress UI)
@app.route("/transcribe_start", methods=["POST"])
def transcribe_start():
    transcription_method = request.form.get("transcription_method", "local")
    local_model_size = request.form.get("local_model_size", "base")
    cloud_model = request.form.get("cloud_model", "whisper-1")
    with_timestamps = request.form.get("with_timestamps") in ("on", "true", "1")

    media_url = (request.form.get("media_url") or "").strip()
    file = request.files.get("audio_file")
    if (not file or file.filename == "") and not media_url:
        return jsonify({"error": "No file or URL provided!"}), 400

    # Persist file to temp dir for background processing
    temp_dir = tempfile.mkdtemp(prefix="job_")
    input_path = os.path.join(temp_dir, (file.filename if file and file.filename else "audio.mp3"))
    if media_url:
        # We'll download within the worker to report progress
        pass
    else:
        file.save(input_path)

    job_id = str(uuid.uuid4())
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "Gestartet", "percent": 0, "cancelled": False}

    # Create DB entry
    session = SessionLocal()
    db_job = TranscriptionJob(
        id=job_id,
        user_email=(getattr(g, "user_email", None) or None),
        source_type=("url" if media_url else "file"),
        source_url=(media_url or None),
        original_filename=(os.path.basename(input_path) if (file and file.filename) else None),
        method=transcription_method,
        local_model_size=local_model_size,
        cloud_model=cloud_model,
        with_timestamps=with_timestamps,
        status="running",
        percent=0,
        started_at=datetime.utcnow(),
    )
    session.add(db_job)
    session.commit()
    session.close()

    def worker():
        try:
            progress_cb = _make_progress_cb(job_id)
            local_input = input_path
            if media_url:
                # Download first with progress 0-30
                _update_job(job_id, status="Lade Audio von URL…", percent=1)
                try:
                    local_input = download_audio_from_url(media_url, temp_dir, progress_cb)
                except Exception as de:
                    raise RuntimeError(str(de))

            # Now transcribe (map internal progress to 30-100)
            def mapped_progress(status: str, pct: int):
                # pct in 0..100 -> 30..100
                mapped = 30 + int(max(0, min(100, pct)) * 0.70)
                _update_job(job_id, status=status, percent=mapped)

            def cancel_check():
                with JOBS_LOCK:
                    return JOBS.get(job_id, {}).get("cancelled", False)

            start_ts = time.time()
            if transcription_method == "local":
                text = transcribe_local_from_path(local_input, local_model_size, mapped_progress, return_segments=with_timestamps, cancel_check=cancel_check)
            else:
                if not OPENAI_API_KEY:
                    raise RuntimeError("No API key configured for cloud transcription!")
                # gpt-4o(-mini)-transcribe liefert derzeit keine Timestamps -> ignorieren
                want_segments = with_timestamps and not cloud_model.startswith("gpt-4o")
                text = transcribe_with_openai_api_path(local_input, cloud_model, mapped_progress, return_segments=want_segments, cancel_check=cancel_check)
            # Detect cancellation result from cloud path
            if isinstance(text, str) and text.strip().lower() == "cancelled":
                raise RuntimeError("Cancelled")
            _update_job(job_id, status="Fertig", percent=100, result=text)

            # Update DB on success
            session2 = SessionLocal()
            dbj = session2.get(TranscriptionJob, job_id)
            if dbj:
                dbj.status = "completed"
                dbj.percent = 100
                if isinstance(text, dict):
                    dbj.result_text = text.get("text") or ""
                    try:
                        dbj.result_segments = json.dumps(text.get("segments") or [])
                    except Exception:
                        dbj.result_segments = None
                else:
                    dbj.result_text = str(text)
                dbj.finished_at = datetime.utcnow()
                try:
                    dbj.duration_seconds = int(time.time() - start_ts)
                except Exception:
                    dbj.duration_seconds = None
                session2.add(dbj)
                session2.commit()
            session2.close()
        except Exception as e:
            _update_job(job_id, status="Fehler", percent=100, error=str(e))
            # Update DB on error/cancel
            session3 = SessionLocal()
            dbj = session3.get(TranscriptionJob, job_id)
            if dbj:
                cancelled = "Cancelled" in str(e) or (JOBS.get(job_id, {}).get("cancelled", False))
                dbj.status = "cancelled" if cancelled else "error"
                dbj.error = str(e)
                dbj.finished_at = datetime.utcnow()
                session3.add(dbj)
                session3.commit()
            session3.close()
        finally:
            # Clean up temp dir
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/transcribe_status/<job_id>", methods=["GET"])
def transcribe_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job id"}), 404
    # Permission: non-admins can only see their own jobs
    session = SessionLocal()
    dbj = session.get(TranscriptionJob, job_id)
    session.close()
    if not _is_current_user_admin():
        if dbj and dbj.user_email and dbj.user_email != getattr(g, "user_email", None):
            return jsonify({"error": "forbidden"}), 403
    return jsonify(job)


@app.route("/jobs/active", methods=["GET"])
def jobs_active():
    # Return in-memory active jobs enriched with DB metadata; filter by user if not admin
    session = SessionLocal()
    payload = []
    is_admin = _is_current_user_admin()
    current = getattr(g, "user_email", None)
    with JOBS_LOCK:
        for jid, j in JOBS.items():
            dbj = session.get(TranscriptionJob, jid)
            if not is_admin:
                if dbj and dbj.user_email and dbj.user_email != current:
                    continue
            payload.append({
                "id": jid,
                "status": j.get("status"),
                "percent": j.get("percent", 0),
                "error": j.get("error"),
                "method": getattr(dbj, "method", None) if dbj else None,
                "model": (dbj.cloud_model if dbj and dbj.method == "cloud" else (dbj.local_model_size if dbj else None)),
                "source_type": getattr(dbj, "source_type", None) if dbj else None,
                "source_url": getattr(dbj, "source_url", None) if dbj else None,
                "started_at": (dbj.started_at.isoformat() + "Z") if (dbj and dbj.started_at) else None,
                "user_email": getattr(dbj, "user_email", None) if dbj else None,
            })
    session.close()
    return jsonify(payload)


@app.route("/jobs/cancel/<job_id>", methods=["POST"])
def jobs_cancel(job_id):
    ok = _job_set_cancel(job_id)
    if not ok:
        return jsonify({"error": "unknown job id"}), 404
    # Permission check: only owner or admin can cancel
    session = SessionLocal()
    dbj = session.get(TranscriptionJob, job_id)
    session.close()
    if not _is_current_user_admin():
        if dbj and dbj.user_email and dbj.user_email != getattr(g, "user_email", None):
            return jsonify({"error": "forbidden"}), 403
    return jsonify({"ok": True})


@app.route("/history/list", methods=["GET"])
def history_list():
    session = SessionLocal()
    q = session.query(TranscriptionJob).filter(TranscriptionJob.status.in_(["completed", "error", "cancelled"]))
    if not _is_current_user_admin():
        q = q.filter(TranscriptionJob.user_email == getattr(g, "user_email", None))
    q = q.order_by(TranscriptionJob.finished_at.desc().nullslast(), TranscriptionJob.started_at.desc())
    limit = int(request.args.get("limit", "100"))
    items = q.limit(limit).all()
    out = []
    for it in items:
        out.append({
            "id": it.id,
            "status": it.status,
            "percent": it.percent,
            "error": it.error,
            "method": it.method,
            "model": it.cloud_model if it.method == "cloud" else it.local_model_size,
            "source_type": it.source_type,
            "source_url": it.source_url,
            "started_at": it.started_at.isoformat() + "Z" if it.started_at else None,
            "finished_at": it.finished_at.isoformat() + "Z" if it.finished_at else None,
            "duration_seconds": it.duration_seconds,
            "text_len": len(it.result_text or ""),
            "user_email": it.user_email or "API",
        })
    session.close()
    return jsonify(out)


@app.route("/history/item/<job_id>", methods=["GET"])
def history_item(job_id):
    session = SessionLocal()
    it = session.get(TranscriptionJob, job_id)
    if not it:
        session.close()
        return jsonify({"error": "not found"}), 404
    if not _is_current_user_admin():
        if it.user_email and it.user_email != getattr(g, "user_email", None):
            session.close()
            return jsonify({"error": "forbidden"}), 403
    payload = {
        "id": it.id,
        "status": it.status,
        "percent": it.percent,
        "error": it.error,
        "method": it.method,
        "model": it.cloud_model if it.method == "cloud" else it.local_model_size,
        "source_type": it.source_type,
        "source_url": it.source_url,
        "started_at": it.started_at.isoformat() + "Z" if it.started_at else None,
        "finished_at": it.finished_at.isoformat() + "Z" if it.finished_at else None,
        "duration_seconds": it.duration_seconds,
        "text": it.result_text or "",
        "segments": None,
        "user_email": it.user_email or "API",
    }
    try:
        if it.result_segments:
            payload["segments"] = json.loads(it.result_segments)
    except Exception:
        payload["segments"] = None
    session.close()
    return jsonify(payload)


@app.route("/history/delete/<job_id>", methods=["POST", "DELETE"])
def history_delete(job_id):
    session = SessionLocal()
    it = session.get(TranscriptionJob, job_id)
    if not it:
        session.close()
        return jsonify({"error": "not found"}), 404
    if not _is_current_user_admin():
        if it.user_email and it.user_email != getattr(g, "user_email", None):
            session.close()
            return jsonify({"error": "forbidden"}), 403
    session.delete(it)
    session.commit()
    session.close()
    return jsonify({"ok": True})


def _is_current_user_admin() -> bool:
    if not getattr(g, "user_email", None):
        return False
    session = SessionLocal()
    u = session.get(User, g.user_email)
    session.close()
    return bool(u and u.is_admin)


@app.route("/users/list", methods=["GET"])
def users_list():
    if not CF_ACCESS_ENFORCE:
        return jsonify({"error": "Cloudflare auth not enabled"}), 400
    if not _is_current_user_admin():
        # Non-admins can see themselves only
        session = SessionLocal()
        me = session.get(User, getattr(g, "user_email", None))
        session.close()
        if not me:
            return jsonify([])
        return jsonify([{ "email": me.email, "is_admin": bool(me.is_admin),
                          "first_seen": me.first_seen.isoformat()+"Z" if me.first_seen else None,
                          "last_seen": me.last_seen.isoformat()+"Z" if me.last_seen else None }])
    session = SessionLocal()
    users = session.query(User).order_by(User.first_seen.asc().nullsfirst()).all()
    out = []
    for u in users:
        out.append({
            "email": u.email,
            "is_admin": bool(u.is_admin),
            "first_seen": u.first_seen.isoformat()+"Z" if u.first_seen else None,
            "last_seen": u.last_seen.isoformat()+"Z" if u.last_seen else None,
        })
    session.close()
    return jsonify(out)


@app.route("/users/toggle_admin", methods=["POST"])
def users_toggle_admin():
    if not CF_ACCESS_ENFORCE:
        return jsonify({"error": "Cloudflare auth not enabled"}), 400
    if not _is_current_user_admin():
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    if not email:
        return jsonify({"error": "missing email"}), 400
    session = SessionLocal()
    u = session.get(User, email)
    if not u:
        session.close()
        return jsonify({"error": "not found"}), 404
    # Prevent demoting the last admin
    if u.is_admin:
        admins = session.query(User).filter(User.is_admin == True).all()
        if len(admins) <= 1:
            session.close()
            return jsonify({"error": "cannot demote the last admin"}), 400
    u.is_admin = not u.is_admin
    session.add(u)
    session.commit()
    session.close()
    return jsonify({"ok": True, "email": email, "is_admin": u.is_admin})

# OpenAI API-compatible endpoint for transcriptions
@app.route("/v1/audio/transcriptions", methods=["POST"])
def api_transcribe():
    # Check API key if configured
    if LOCAL_API_KEY:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer ") or auth_header.split(" ")[1] != LOCAL_API_KEY:
            return jsonify({"error": "Invalid API key"}), 401
    
    # Check audio file
    if "file" not in request.files:
        return jsonify({"error": "No audio file submitted"}), 400
    
    file = request.files["file"]
    # Check if this is a request for whisper-1 compatibility
    requested_model = request.form.get("model", DEFAULT_API_MODEL)
    
    # For OpenAI API compatibility, map whisper-1 to the configured default model
    if requested_model == "whisper-1":
        model_size = DEFAULT_API_MODEL
    else:
        # For direct local model specification (tiny, base, small, medium, large)
        model_size = requested_model
    
    # Determine desired response format (json or plain text)
    response_format = request.form.get("response_format", "json").lower()
    try:
        want_verbose = response_format == "verbose_json"
        result = transcribe_with_local_model(file, model_size, return_segments=want_verbose)
        if response_format == "text":
            return Response(result if isinstance(result, str) else result.get("text", ""), mimetype="text/plain")
        if response_format == "verbose_json":
            if isinstance(result, dict):
                return jsonify({"text": result.get("text", ""), "segments": result.get("segments") or []})
            else:
                return jsonify({"text": result, "segments": []})
        # Default to JSON format
        return jsonify({"text": result if isinstance(result, str) else result.get("text", "")})
    except Exception as e:
        # Return error in JSON for consistency with OpenAI API
        return jsonify({"error": str(e)}), 500

_DB_READY = False
@app.before_request
def _ensure_db_ready():
    global _DB_READY
    if not _DB_READY:
        init_db()
        _DB_READY = True


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")