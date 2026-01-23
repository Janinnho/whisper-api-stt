"""
Whisper API STT - Main Application
A web app for audio transcription using local Whisper and OpenAI Cloud.
"""
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
from datetime import datetime, timedelta

from flask import Flask, render_template, request, jsonify, Response, g, redirect, url_for
import requests
import whisper
from urllib.parse import urlparse
import yt_dlp
from flask_cors import CORS

# Import new modules
from version import VERSION
from models import (
    TranscriptionJob, User, ApiKey, Session as DbSession,
    SessionLocal, init_db, generate_uuid, DB_PATH, engine, Base
)
from config import (
    get_setting, set_setting, SettingsManager,
    is_auth_enabled, is_setup_completed
)
from auth import auth_bp, validate_api_key, login_required, admin_required
from admin import admin_bp

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Register blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)

# Environment variables (only OPENAI_API_KEY remains as env var)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    print("Note: No OPENAI_API_KEY found. Cloud transcription will not be available.")

# Load local Whisper model (will be downloaded on first call)
local_model = None
current_model_size = None
JOBS_LOCK = threading.Lock()
JOBS = {}
DB_INIT_LOCK = threading.Lock()
_DB_READY = False
_WATCHDOG_STARTED = False


@app.before_request
def _ensure_db_ready():
    """Initialize the database on first request in a thread-safe manner."""
    global _DB_READY
    if not _DB_READY:
        with DB_INIT_LOCK:
            if not _DB_READY:
                init_db()
                SettingsManager.initialize()
                _DB_READY = True
    # Start watchdog once
    global _WATCHDOG_STARTED
    if not _WATCHDOG_STARTED:
        with DB_INIT_LOCK:
            if not _WATCHDOG_STARTED:
                t = threading.Thread(target=_watchdog_loop, daemon=True)
                t.start()
                _WATCHDOG_STARTED = True


@app.before_request
def _auth_middleware():
    """Handle authentication for each request."""
    g.user = None
    g.user_email = None
    g.is_admin = False

    path = request.path or ""

    # Skip auth for static files
    if path.startswith('/static/'):
        return None

    # Check if auth is enabled
    if not is_auth_enabled():
        # Auth disabled - allow anonymous access
        return None

    # Check for setup wizard redirect
    if not is_setup_completed():
        if not path.startswith('/admin/setup') and not path.startswith('/static/'):
            return redirect(url_for('admin.setup_page'))

    # Skip auth for login pages
    if path in ['/login-access', '/auth/login', '/auth/oidc/authorize', '/auth/oidc/callback']:
        return None

    # API endpoint - check API key if required
    if path.startswith('/v1/audio/transcriptions'):
        if get_setting('api_key_required', False):
            auth_header = request.headers.get('Authorization', '')
            api_key = validate_api_key(auth_header)
            if not api_key:
                # Backward compatibility: check legacy LOCAL_API_KEY env var
                legacy_key = os.getenv('LOCAL_API_KEY')
                if legacy_key:
                    if not auth_header.startswith('Bearer ') or auth_header.split(' ', 1)[1] != legacy_key:
                        return jsonify({'error': 'Invalid API key'}), 401
                else:
                    return jsonify({'error': 'Invalid API key'}), 401
        return None

    # Try session authentication
    from auth import SESSION_COOKIE_NAME, validate_session, get_or_create_header_user
    session_id = request.cookies.get(SESSION_COOKIE_NAME + '_id')
    session_token = request.cookies.get(SESSION_COOKIE_NAME + '_token')

    if session_id and session_token:
        user = validate_session(session_id, session_token)
        if user:
            g.user = user
            g.user_email = user.email
            g.is_admin = user.is_admin
            return None

    # Try HTTP header authentication
    if get_setting('auth_http_header_enabled', False):
        header_name = get_setting('auth_http_header_name', 'CF-Access-Authenticated-User-Email')
        header_value = request.headers.get(header_name)
        if header_value:
            user = get_or_create_header_user(header_value)
            if user:
                g.user = user
                g.user_email = user.email
                g.is_admin = user.is_admin
                return None

    # Not authenticated - redirect based on default method
    default_method = get_setting('auth_default_method', 'local')

    if default_method == 'oidc' and get_setting('auth_oidc_enabled', False):
        return redirect(url_for('auth.oidc_authorize'))
    else:
        return redirect(url_for('auth.login_page'))


def load_local_model(model_size="base"):
    global local_model, current_model_size
    if local_model is None or current_model_size != model_size:
        print(f"Loading local Whisper model ({model_size})...")
        local_model = whisper.load_model(model_size)
        current_model_size = model_size
    return local_model


def transcribe_with_local_model(audio_file, model_size="base", progress: Optional[Callable[[str, int], None]] = None, return_segments: bool = False):
    with tempfile.NamedTemporaryFile(delete=True) as temp_file:
        audio_file.save(temp_file.name)
        return transcribe_local_from_path(temp_file.name, model_size, progress, return_segments)


def transcribe_local_from_path(file_path: str, model_size="base", progress: Optional[Callable[[str, int], None]] = None, return_segments: bool = False, cancel_check: Optional[Callable[[], bool]] = None):
    # Get settings
    chunk_duration = get_setting('chunk_duration_seconds', 600)
    reencode_bitrate = get_setting('reencode_bitrate', '64k')

    if progress:
        progress("Loading local Whisper model", 5)
    model = load_local_model(model_size)
    if progress:
        progress("Transcribing locally...", 10)

    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", file_path
        ], text=True)
        duration = float(out.strip())
    except Exception:
        duration = 0.0

    if duration and duration > chunk_duration * 1.5:
        tmpdir = tempfile.mkdtemp(prefix="local_chunk_")
        try:
            out_pattern = os.path.join(tmpdir, "chunk_%03d.mp3")
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", file_path,
                "-ac", "1", "-ar", "16000", "-b:a", reencode_bitrate,
                "-f", "segment", "-segment_time", str(chunk_duration),
                "-reset_timestamps", "1",
                out_pattern,
            ]
            subprocess.run(cmd, check=True)
        except Exception as e:
            result = model.transcribe(file_path)
            if progress:
                progress("Done", 100)
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

        durations = []
        for p in chunks:
            try:
                out = subprocess.check_output([
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=nw=1:nk=1", p
                ], text=True)
                durations.append(float(out.strip()))
            except Exception:
                durations.append(float(chunk_duration))

        for idx, ch in enumerate(chunks, start=1):
            if cancel_check and cancel_check():
                if progress:
                    progress("Cancelled", 100)
                shutil.rmtree(tmpdir, ignore_errors=True)
                raise RuntimeError("Cancelled")
            if progress:
                percent = 10 + int(85 * (idx - 1) / total)
                progress(f"Transcribing chunk {idx}/{total}", percent)
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
                cum_offset += float(chunk_duration)
        merged = "\n\n".join([p for p in parts if p])
        if progress:
            progress("Done", 100)
        if not return_segments:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return merged
        shutil.rmtree(tmpdir, ignore_errors=True)
        return {"text": merged, "segments": all_segments or None}

    result = model.transcribe(file_path)
    if progress:
        progress("Done", 100)
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
        model = "whisper-1"
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_ext = os.path.splitext(audio_file.filename or "")[1] or ".bin"
        input_path = os.path.join(tmpdir, f"input{orig_ext}")
        audio_file.save(input_path)
        return transcribe_with_openai_api_path(input_path, model, progress, return_segments)


def transcribe_with_openai_api_path(input_path: str, model="whisper-1", progress: Optional[Callable[[str, int], None]] = None, return_segments: bool = False, cancel_check: Optional[Callable[[], bool]] = None):
    valid_models = ["whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"]
    if model not in valid_models:
        model = "whisper-1"

    # Get settings
    max_cloud_file_mb = get_setting('max_cloud_file_mb', 20)
    chunk_duration = get_setting('chunk_duration_seconds', 600)
    reencode_bitrate = get_setting('reencode_bitrate', '64k')

    file_size_bytes = os.path.getsize(input_path)
    size_mb = file_size_bytes / (1024 * 1024)

    def _send_to_openai(file_path: str, override_content_type: str = None, want_verbose: bool = False) -> dict:
        ct = override_content_type or (mimetypes.guess_type(file_path)[0] or "application/octet-stream")
        if progress:
            progress(f"Sending to OpenAI: {os.path.basename(file_path)}", 0)
        data_fields = [("model", model)]
        if want_verbose:
            data_fields.append(("response_format", "verbose_json"))
            if model.startswith("gpt-4o"):
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

    if size_mb <= max_cloud_file_mb:
        if progress:
            progress("Direct upload to OpenAI", 10)
        resp = _send_to_openai(input_path, want_verbose=return_segments)
        if "__error__" in resp:
            return f"Transcription error: {resp['__error__']}"
        if progress:
            progress("Done", 100)
        text = resp.get("text", "")
        if not return_segments:
            return text or "No transcription found."
        segments = _extract_segments(resp)
        return {"text": text or "No transcription found.", "segments": segments}

    if progress:
        progress("File too large - segmenting audio...", 5)
    tmpdir = tempfile.mkdtemp(prefix="chunk_")
    try:
        out_pattern = os.path.join(tmpdir, "chunk_%03d.mp3")
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", input_path,
            "-ac", "1", "-ar", "16000", "-b:a", reencode_bitrate,
            "-f", "segment", "-segment_time", str(chunk_duration),
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
            percent = 10 + int(85 * (idx - 1) / total)
            progress(f"Transcribing chunk {idx}/{total}", percent)
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
                cumulative_offset += float(chunk_duration)

    merged = "\n\n".join([p for p in parts if p])
    if progress:
        progress("Done", 100)
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
            "youtube.com", "www.youtube.com", "m.youtube.com",
            "music.youtube.com", "youtu.be", "www.youtu.be",
            "twitch.tv", "www.twitch.tv",
        )
        return any(host.endswith(h) for h in allowed_hosts)
    except Exception:
        return False


def download_audio_from_url(media_url: str, dest_dir: str, progress: Optional[Callable[[str, int], None]] = None) -> str:
    if not _is_supported_media_url(media_url):
        raise ValueError("Only YouTube or Twitch URLs are supported.")

    os.makedirs(dest_dir, exist_ok=True)
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
                mapped = min(30, max(0, int(pct * 0.30)))
                if mapped != last_percent:
                    last_percent = mapped
                    if progress:
                        progress(f"Downloading audio... {pct}%", mapped)
            elif d.get('status') == 'finished':
                if progress:
                    progress("Download complete - extracting audio...", 30)
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
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([media_url])
    except Exception as e:
        raise RuntimeError(f"Download failed: {str(e)}")

    final_path = os.path.join(dest_dir, "download.mp3")
    if not os.path.isfile(final_path):
        candidates = [
            os.path.join(dest_dir, f) for f in os.listdir(dest_dir)
            if f.lower().endswith(('.mp3', '.m4a', '.aac', '.wav', '.ogg', '.webm'))
        ]
        if not candidates:
            raise RuntimeError("Audio extraction failed - no audio file produced.")
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


def _watchdog_loop():
    job_timeout = get_setting('job_timeout_seconds', 43200)
    while True:
        try:
            now_ts = time.time()
            timed_out_ids = []
            with JOBS_LOCK:
                for jid, j in JOBS.items():
                    started = j.get("started_ts") or 0
                    if started and (now_ts - started) > job_timeout:
                        timed_out_ids.append(jid)
            for jid in timed_out_ids:
                _job_set_cancel(jid)
                _update_job(jid, status="Timeout - Cancelled", percent=100)
                session = SessionLocal()
                dbj = session.get(TranscriptionJob, jid)
                if dbj and dbj.status in ("running", "queued"):
                    dbj.status = "cancelled"
                    dbj.error = "Timed out"
                    dbj.finished_at = datetime.utcnow()
                    session.add(dbj)
                    session.commit()
                session.close()
        except Exception:
            pass
        time.sleep(30)


def _is_current_user_admin() -> bool:
    if g.get('user'):
        return g.user.is_admin
    if not getattr(g, "user_email", None):
        return False
    session = SessionLocal()
    u = session.query(User).filter(User.email == g.user_email).first()
    session.close()
    return bool(u and u.is_admin)


@app.route("/", methods=["GET", "POST"])
def index():
    transcription = None
    transcription_method = "local"
    local_model_size = "base"
    cloud_model = "whisper-1"
    cloud_available = OPENAI_API_KEY is not None

    if request.method == "POST":
        transcription_method = request.form.get("transcription_method", "local")
        local_model_size = request.form.get("local_model_size", "base")
        cloud_model = request.form.get("cloud_model", "whisper-1")

        if request.form.get("action") == "save_settings":
            set_setting('api_default_model', request.form.get("api_model", "base"),
                       g.user.email if g.get('user') else None)
            return render_template(
                "index.html",
                transcription=transcription,
                selected_method=transcription_method,
                local_model_size=local_model_size,
                cloud_model=cloud_model,
                api_model=get_setting('api_default_model', 'base'),
                cloud_available=cloud_available,
                settings_saved=True,
                version=VERSION,
                user=g.get('user'),
                auth_enabled=is_auth_enabled()
            )

        media_url = (request.form.get("media_url") or "").strip()
        file = request.files.get("audio_file")
        if (not file or file.filename == "") and not media_url:
            transcription = "No file or URL provided!"
        else:
            try:
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
                    else:
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
        api_model=get_setting('api_default_model', 'base'),
        cloud_available=cloud_available,
        version=VERSION,
        user=g.get('user'),
        auth_enabled=is_auth_enabled(),
        is_admin=_is_current_user_admin()
    )


@app.route("/transcribe", methods=["POST"])
def transcribe_ajax():
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
                if cloud_model.startswith("gpt-4o"):
                    with_timestamps = False
                result = transcribe_with_openai_api_path(input_path, cloud_model, return_segments=with_timestamps)
    except Exception as e:
        return jsonify({"error": f"Transcription error: {str(e)}"}), 500
    if isinstance(result, dict):
        return jsonify({"transcription": result.get("text", ""), "segments": result.get("segments")})
    return jsonify({"transcription": str(result), "segments": None})


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

    temp_dir = tempfile.mkdtemp(prefix="job_")
    input_path = os.path.join(temp_dir, (file.filename if file and file.filename else "audio.mp3"))
    if not media_url and file:
        file.save(input_path)

    job_id = str(uuid.uuid4())
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "Started", "percent": 0, "cancelled": False, "started_ts": time.time()}

    # Get user info
    user_id = g.user.id if g.get('user') else None
    user_email = g.user.email if g.get('user') else (getattr(g, 'user_email', None) or None)

    session = SessionLocal()
    db_job = TranscriptionJob(
        id=job_id,
        user_id=user_id,
        user_email=user_email,
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
                _update_job(job_id, status="Downloading audio from URL...", percent=1)
                try:
                    local_input = download_audio_from_url(media_url, temp_dir, progress_cb)
                except Exception as de:
                    raise RuntimeError(str(de))

            def mapped_progress(status: str, pct: int):
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
                want_segments = with_timestamps and not cloud_model.startswith("gpt-4o")
                text = transcribe_with_openai_api_path(local_input, cloud_model, mapped_progress, return_segments=want_segments, cancel_check=cancel_check)
            if isinstance(text, str) and text.strip().lower() == "cancelled":
                raise RuntimeError("Cancelled")
            _update_job(job_id, status="Done", percent=100, result=text)

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
            _update_job(job_id, status="Error", percent=100, error=str(e))
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
    session = SessionLocal()
    dbj = session.get(TranscriptionJob, job_id)
    session.close()
    if not dbj and not job:
        return jsonify({"error": "unknown job id"}), 404

    if not _is_current_user_admin():
        owner_id = getattr(dbj, "user_id", None) if dbj else None
        owner_email = getattr(dbj, "user_email", None) if dbj else None
        current_id = g.user.id if g.get('user') else None
        current_email = g.user.email if g.get('user') else getattr(g, 'user_email', None)
        if owner_id and owner_id != current_id:
            if owner_email and owner_email != current_email:
                return jsonify({"error": "forbidden"}), 403

    if job:
        return jsonify(job)
    resp = {
        "status": None,
        "percent": getattr(dbj, "percent", 0) if dbj else 0,
        "result": None,
        "error": None,
    }
    if dbj:
        if dbj.status == "completed":
            resp["status"] = "Done"
            resp["percent"] = 100
            segs = None
            try:
                if dbj.result_segments:
                    segs = json.loads(dbj.result_segments)
            except Exception:
                segs = None
            resp["result"] = {"text": dbj.result_text or "", "segments": segs}
        elif dbj.status in ("error", "cancelled"):
            resp["status"] = "Error" if dbj.status == "error" else "Cancelled"
            resp["percent"] = 100
            resp["error"] = dbj.error or ("Cancelled" if dbj.status == "cancelled" else "Error")
        else:
            resp["status"] = "Running..."
            resp["percent"] = int(dbj.percent or 0)
    return jsonify(resp)


@app.route("/jobs/active", methods=["GET"])
def jobs_active():
    session = SessionLocal()
    payload = []
    is_admin = _is_current_user_admin()
    current_id = g.user.id if g.get('user') else None
    current_email = g.user.email if g.get('user') else getattr(g, 'user_email', None)
    with JOBS_LOCK:
        for jid, j in list(JOBS.items()):
            dbj = session.get(TranscriptionJob, jid)
            if not dbj or dbj.status not in ("running", "queued"):
                continue
            if not is_admin:
                if dbj.user_id and dbj.user_id != current_id:
                    if dbj.user_email and dbj.user_email != current_email:
                        continue
            payload.append({
                "id": jid,
                "status": j.get("status"),
                "percent": j.get("percent", 0),
                "error": j.get("error"),
                "method": dbj.method if dbj else None,
                "model": (dbj.cloud_model if dbj and dbj.method == "cloud" else (dbj.local_model_size if dbj else None)),
                "source_type": dbj.source_type if dbj else None,
                "source_url": dbj.source_url if dbj else None,
                "started_at": (dbj.started_at.isoformat() + "Z") if (dbj and dbj.started_at) else None,
                "user_email": dbj.user_email if dbj else None,
            })
    session.close()
    return jsonify(payload)


@app.route("/jobs/cancel/<job_id>", methods=["POST"])
def jobs_cancel(job_id):
    session = SessionLocal()
    dbj = session.get(TranscriptionJob, job_id)
    session.close()
    if not _is_current_user_admin():
        current_id = g.user.id if g.get('user') else None
        current_email = g.user.email if g.get('user') else getattr(g, 'user_email', None)
        if dbj and dbj.user_id and dbj.user_id != current_id:
            if dbj.user_email and dbj.user_email != current_email:
                return jsonify({"error": "forbidden"}), 403
    ok = _job_set_cancel(job_id)
    if not ok:
        return jsonify({"error": "unknown job id"}), 404
    _update_job(job_id, status="Cancel requested", percent=None)
    return jsonify({"ok": True})


@app.route("/history/list", methods=["GET"])
def history_list():
    session = SessionLocal()
    q = session.query(TranscriptionJob).filter(TranscriptionJob.status.in_(["completed", "error", "cancelled"]))
    if not _is_current_user_admin():
        current_id = g.user.id if g.get('user') else None
        current_email = g.user.email if g.get('user') else getattr(g, 'user_email', None)
        if current_id:
            q = q.filter((TranscriptionJob.user_id == current_id) | (TranscriptionJob.user_email == current_email))
        elif current_email:
            q = q.filter(TranscriptionJob.user_email == current_email)
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
        current_id = g.user.id if g.get('user') else None
        current_email = g.user.email if g.get('user') else getattr(g, 'user_email', None)
        if it.user_id and it.user_id != current_id:
            if it.user_email and it.user_email != current_email:
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
        current_id = g.user.id if g.get('user') else None
        current_email = g.user.email if g.get('user') else getattr(g, 'user_email', None)
        if it.user_id and it.user_id != current_id:
            if it.user_email and it.user_email != current_email:
                session.close()
                return jsonify({"error": "forbidden"}), 403
    session.delete(it)
    session.commit()
    session.close()
    return jsonify({"ok": True})


@app.route("/users/list", methods=["GET"])
def users_list():
    if not is_auth_enabled():
        return jsonify({"error": "Authentication not enabled"}), 400
    if not _is_current_user_admin():
        if g.get('user'):
            return jsonify([{
                "id": g.user.id,
                "email": g.user.email,
                "name": g.user.name,
                "is_admin": g.user.is_admin,
                "source": g.user.source,
                "first_seen": g.user.first_seen.isoformat() + "Z" if g.user.first_seen else None,
                "last_seen": g.user.last_seen.isoformat() + "Z" if g.user.last_seen else None
            }])
        return jsonify([])
    session = SessionLocal()
    users = session.query(User).order_by(User.created_at.asc().nullsfirst()).all()
    out = []
    for u in users:
        out.append({
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "is_admin": bool(u.is_admin),
            "source": u.source,
            "first_seen": u.first_seen.isoformat() + "Z" if u.first_seen else None,
            "last_seen": u.last_seen.isoformat() + "Z" if u.last_seen else None,
        })
    session.close()
    return jsonify(out)


@app.route("/users/toggle_admin", methods=["POST"])
def users_toggle_admin():
    if not is_auth_enabled():
        return jsonify({"error": "Authentication not enabled"}), 400
    if not _is_current_user_admin():
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    if not email:
        return jsonify({"error": "missing email"}), 400
    session = SessionLocal()
    u = session.query(User).filter(User.email == email).first()
    if not u:
        session.close()
        return jsonify({"error": "not found"}), 404
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


@app.route("/v1/audio/transcriptions", methods=["POST"])
def api_transcribe():
    # API key check is handled by middleware
    if "file" not in request.files:
        return jsonify({"error": "No audio file submitted"}), 400

    file = request.files["file"]
    requested_model = request.form.get("model", get_setting('api_default_model', 'base'))

    if requested_model == "whisper-1":
        model_size = get_setting('api_default_model', 'base')
    else:
        model_size = requested_model

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
        return jsonify({"text": result if isinstance(result, str) else result.get("text", "")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
