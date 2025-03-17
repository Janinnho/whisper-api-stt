FROM python:3.9-slim

# Installiere ffmpeg und andere Abhängigkeiten
RUN apt-get update && apt-get install -y ffmpeg

# Setze Umgebungsvariablen
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# OPENAI_API_KEY kann beim Build mit --build-arg OPENAI_API_KEY=your_key übergeben werden
ARG OPENAI_API_KEY=""
ENV OPENAI_API_KEY=${OPENAI_API_KEY}
# LOCAL_API_KEY für API-Endpunkt kann mit --build-arg oder -e LOCAL_API_KEY übergeben werden
ARG LOCAL_API_KEY=""
ENV LOCAL_API_KEY=${LOCAL_API_KEY}

# Arbeitsverzeichnis festlegen
WORKDIR /app

# Kopiere die requirements.txt und installiere Abhängigkeiten
COPY requirements.txt /app/
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Kopiere den Rest der Dateien
COPY . /app/

# Setze die Flask-App-Variable
ENV FLASK_APP=app.py

# Exponiere den Port
EXPOSE 5000

# Starte die Flask-App
CMD ["flask", "run", "--host=0.0.0.0"]