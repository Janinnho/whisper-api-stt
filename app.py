import os
import tempfile
from flask import Flask, render_template, request
import requests
import whisper

app = Flask(__name__)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    print("Hinweis: Kein OPENAI_API_KEY gefunden. Cloud-Transkription wird nicht verfügbar sein.")

# Lade lokales Whisper-Modell (wird beim ersten Aufruf heruntergeladen)
# "base" ist ein guter Kompromiss zwischen Genauigkeit und Geschwindigkeit
local_model = None

def load_local_model(model_size="base"):
    global local_model
    if local_model is None:
        print(f"Lade lokales Whisper-Modell ({model_size})...")
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
        return json_response.get("text", "Keine Transkription gefunden.")
    else:
        return f"Fehler bei der Transkription: {response.status_code} - {response.text}"

@app.route("/", methods=["GET", "POST"])
def index():
    transcription = None
    transcription_method = "local"  # Standard-Methode ist nun lokal
    cloud_available = OPENAI_API_KEY is not None
    
    if request.method == "POST":
        transcription_method = request.form.get("transcription_method", "local")
        
        if "audio_file" not in request.files:
            transcription = "Keine Datei ausgewählt!"
        else:
            file = request.files["audio_file"]
            if file.filename == "":
                transcription = "Keine Datei ausgewählt!"
            else:
                try:
                    if transcription_method == "local":
                        transcription = transcribe_with_local_model(file)
                    else:  # cloud
                        if not OPENAI_API_KEY:
                            transcription = "Fehler: Kein API-Key für die Cloud-Transkription konfiguriert!"
                        else:
                            transcription = transcribe_with_openai_api(file)
                except Exception as e:
                    transcription = f"Fehler bei der Transkription: {str(e)}"
    return render_template("index.html", transcription=transcription, selected_method=transcription_method, cloud_available=cloud_available)

if __name__ == "__main__":
    app.run(debug=True)