import os
import tempfile
import json
from flask import Flask, render_template, request, jsonify
import requests
import whisper

app = Flask(__name__)
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

def transcribe_with_openai_api(audio_file, model="whisper-1"):
    valid_models = ["whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"]
    if model not in valid_models:
        model = "whisper-1"  # Default to whisper-1 if invalid model
        
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
    
    try:
        transcription = transcribe_with_local_model(file, model_size)
        return jsonify({"text": transcription})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")