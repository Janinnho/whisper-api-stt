FROM python:3.11-slim

# Install ffmpeg and runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
	ffmpeg \
	build-essential \
	git \
	&& rm -rf /var/lib/apt/lists/*

# Setze Umgebungsvariablen
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# OPENAI_API_KEY kann beim Build mit --build-arg OPENAI_API_KEY=your_key übergeben werden
ARG OPENAI_API_KEY=""
ENV OPENAI_API_KEY=${OPENAI_API_KEY}
# LOCAL_API_KEY für API-Endpunkt kann mit --build-arg oder -e LOCAL_API_KEY übergeben werden
ARG LOCAL_API_KEY=""
ENV LOCAL_API_KEY=${LOCAL_API_KEY}
 # Optional: Cloudflare Access Enforcement
ARG CF_ACCESS_ENFORCE="false"
ENV CF_ACCESS_ENFORCE=${CF_ACCESS_ENFORCE}

# Database path (mounted volume suggested)
ENV DB_PATH=/data/app.db

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

# Create volume mountpoint for DB persistence
VOLUME ["/data"]

# Start the app with gunicorn for robustness
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "app:app"]
