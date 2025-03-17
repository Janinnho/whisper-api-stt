import os
import tempfile
import json
from flask import Flask, render_template, request, jsonify
import requests
import whisper

app = Flask(__name__)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LOCAL_API_KEY = os.getenv("LOCAL_API_KEY", None)  # Optional API key for local API
if not OPENAI_API_KEY:
    print("Note: No OPENAI_API_KEY found. Cloud transcription will not be available.")

# Load local Whisper model (will be downloaded on first call)
# "base" is a good compromise between accuracy and speed
local_model = None

def load_local_model(model_size="base"):
    global local_model
    if local_model is None:
        print(f"Loading local Whisper model ({model_size})...")
        local_model = whisper.load_model(model_size)
    return local_model

def transcribe_with_local_model(audio_file, model_size="base"):
    model = load_local_model(model_size)
    with tempfile.NamedTemporaryFile(delete=True) as temp_file:
        audio_file.save(temp_file.name)
        result = model.transcribe(temp_file.name)
    return result["text"]

def transcribe_with_openai_api(audio_file):
    response = requests.post(
        "https://api.openai.com/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        files={"file": (audio_file.filename, audio_file, audio_file.content_type)},
        data={"model": "whisper-1"}
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
    cloud_available = OPENAI_API_KEY is not None
    
    if request.method == "POST":
        transcription_method = request.form.get("transcription_method", "local")
        
        if "audio_file" not in request.files:
            transcription = "No file selected!"
        else:
            file = request.files["audio_file"]
            if file.filename == "":
                transcription = "No file selected!"
            else:
                try:
                    if transcription_method == "local":
                        transcription = transcribe_with_local_model(file)
                    else:  # cloud
                        if not OPENAI_API_KEY:
                            transcription = "Error: No API key configured for cloud transcription!"
                        else:
                            transcription = transcribe_with_openai_api(file)
                except Exception as e:
                    transcription = f"Transcription error: {str(e)}"
    return render_template("index.html", transcription=transcription, selected_method=transcription_method, cloud_available=cloud_available)

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
    model_size = request.form.get("model", "base")  # 'tiny', 'base', 'small', 'medium', 'large'
    
    try:
        transcription = transcribe_with_local_model(file, model_size)
        return jsonify({"text": transcription})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)