import os
import tempfile
import json
from flask import Flask, render_template, request, jsonify, Response
import requests
import whisper
from pydub import AudioSegment
import io

from flask_cors import CORS
app = Flask(__name__)
# Enable CORS for all routes to allow external clients (e.g., Dictate) to communicate with this API
CORS(app)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LOCAL_API_KEY = os.getenv("LOCAL_API_KEY", None)  # Optional API key for local API
DEFAULT_API_MODEL = "base"  # Default model for API requests

if not OPENAI_API_KEY:
    print("Note: No OPENAI_API_KEY found. Cloud transcription will not be available.")

# Load local Whisper model (will be downloaded on first call)
local_model = None
current_model_size = None

def load_local_model(model_size="base"):
    global local_model, current_model_size
    if local_model is None or current_model_size != model_size:
        print(f"Loading local Whisper model ({model_size})...")
        local_model = whisper.load_model(model_size)
        current_model_size = model_size
    return local_model

def transcribe_with_local_model(audio_file, model_size="base"):
    model = load_local_model(model_size)
    with tempfile.NamedTemporaryFile(delete=True) as temp_file:
        audio_file.save(temp_file.name)
        result = model.transcribe(temp_file.name)
    return result["text"]

def get_file_size_mb(file_obj):
    """Get file size in MB."""
    file_obj.seek(0, 2)  # Seek to end
    size_bytes = file_obj.tell()
    file_obj.seek(0)  # Reset to beginning
    return size_bytes / (1024 * 1024)

def split_audio_file(file_obj, max_chunk_size_mb=18):  # Keep chunks under 18MB for safety
    """Split audio file into smaller chunks that stay under the size limit."""
    # Read the file content
    file_content = file_obj.read()
    file_obj.seek(0)  # Reset file pointer
    
    # Load audio with pydub
    audio = AudioSegment.from_file(io.BytesIO(file_content))
    
    # Calculate target chunk duration based on the original file size and length
    total_size_mb = len(file_content) / (1024 * 1024)
    total_duration_ms = len(audio)
    
    # Calculate how many chunks we need to stay under the size limit
    estimated_chunks_needed = max(1, int(total_size_mb / max_chunk_size_mb) + 1)
    target_chunk_duration_ms = total_duration_ms // estimated_chunks_needed
    
    # Ensure minimum chunk duration of 30 seconds to avoid too many tiny chunks
    min_chunk_duration_ms = 30 * 1000  # 30 seconds
    target_chunk_duration_ms = max(target_chunk_duration_ms, min_chunk_duration_ms)
    
    chunks = []
    for i in range(0, len(audio), target_chunk_duration_ms):
        chunk = audio[i:i + target_chunk_duration_ms]
        
        # Skip empty chunks
        if len(chunk) == 0:
            continue
        
        # Export chunk to bytes
        chunk_io = io.BytesIO()
        chunk.export(chunk_io, format="wav")
        chunk_io.seek(0)
        
        # Verify chunk size
        chunk_size_mb = len(chunk_io.getvalue()) / (1024 * 1024)
        if chunk_size_mb > max_chunk_size_mb:
            # If chunk is still too large, split it further
            chunk_io.close()
            # Recursively split this chunk with smaller duration
            smaller_duration = target_chunk_duration_ms // 2
            sub_chunks = []
            for j in range(0, len(chunk), smaller_duration):
                sub_chunk = chunk[j:j + smaller_duration]
                if len(sub_chunk) > 0:  # Skip empty sub-chunks
                    sub_chunk_io = io.BytesIO()
                    sub_chunk.export(sub_chunk_io, format="wav")
                    sub_chunk_io.seek(0)
                    sub_chunks.append(sub_chunk_io)
            chunks.extend(sub_chunks)
        else:
            chunks.append(chunk_io)
    
    return chunks

def transcribe_with_openai_api(audio_file, model="whisper-1"):
    valid_models = ["whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"]
    if model not in valid_models:
        model = "whisper-1"  # Default to whisper-1 if invalid model
    
    # Check file size
    file_size_mb = get_file_size_mb(audio_file)
    
    if file_size_mb <= 20:
        # File is small enough, process normally
        response = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            files={"file": (audio_file.filename, audio_file, audio_file.content_type)},
            data={"model": model}
        )
        if response.status_code == 200:
            json_response = response.json()
            return json_response.get("text", "No transcription found.")
        else:
            return f"Transcription error: {response.status_code} - {response.text}"
    else:
        # File is too large, split it into chunks
        try:
            chunks = split_audio_file(audio_file)
            transcriptions = []
            
            for i, chunk in enumerate(chunks):
                # Create a filename for the chunk
                chunk_filename = f"{audio_file.filename}_chunk_{i+1}.wav"
                
                response = requests.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    files={"file": (chunk_filename, chunk, "audio/wav")},
                    data={"model": model}
                )
                
                if response.status_code == 200:
                    json_response = response.json()
                    transcription = json_response.get("text", "")
                    if transcription:
                        transcriptions.append(transcription)
                else:
                    # Close remaining chunks before returning error
                    for remaining_chunk in chunks[i:]:
                        remaining_chunk.close()
                    return f"Transcription error on chunk {i+1}: {response.status_code} - {response.text}"
                
                # Close the chunk to free memory
                chunk.close()
            
            # Merge all transcriptions with proper spacing
            return " ".join(transcriptions)
            
        except Exception as e:
            return f"Error processing large file: {str(e)}"

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
        
        if "audio_file" not in request.files:
            transcription = "No file selected!"
        else:
            file = request.files["audio_file"]
            if file.filename == "":
                transcription = "No file selected!"
            else:
                try:
                    if transcription_method == "local":
                        transcription = transcribe_with_local_model(file, local_model_size)
                    else:  # cloud
                        if not OPENAI_API_KEY:
                            transcription = "Error: No API key configured for cloud transcription!"
                        else:
                            transcription = transcribe_with_openai_api(file, cloud_model)
                except Exception as e:
                    transcription = f"Transcription error: {str(e)}"
    
    return render_template(
        "index.html", 
        transcription=transcription, 
        selected_method=transcription_method,
        local_model_size=local_model_size,
        cloud_model=cloud_model,
        api_model=DEFAULT_API_MODEL,
        cloud_available=cloud_available
    )
    
@app.route("/transcribe", methods=["POST"])
def transcribe_ajax():
    transcription = None
    transcription_method = request.form.get("transcription_method", "local")
    local_model_size = request.form.get("local_model_size", "base")
    cloud_model = request.form.get("cloud_model", "whisper-1")

    if "audio_file" not in request.files:
        return jsonify({"error": "No file selected!"}), 400
    file = request.files["audio_file"]
    if file.filename == "":
        return jsonify({"error": "No file selected!"}), 400

    try:
        if transcription_method == "local":
            transcription = transcribe_with_local_model(file, local_model_size)
        else:
            if not OPENAI_API_KEY:
                return jsonify({"error": "Error: No API key configured for cloud transcription!"}), 400
            transcription = transcribe_with_openai_api(file, cloud_model)
    except Exception as e:
        return jsonify({"error": f"Transcription error: {str(e)}"}), 500

    return jsonify({"transcription": transcription})

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
        transcription = transcribe_with_local_model(file, model_size)
        if response_format == "text":
            return Response(transcription, mimetype="text/plain")
        # Default to JSON format
        return jsonify({"text": transcription})
    except Exception as e:
        # Return error in JSON for consistency with OpenAI API
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")