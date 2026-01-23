FROM python:3.11-slim

# Install ffmpeg and runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
	ffmpeg \
	build-essential \
	git \
	&& rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# OPENAI_API_KEY is the only required environment variable
# Can be passed at runtime with -e OPENAI_API_KEY=your_key
ARG OPENAI_API_KEY=""
ENV OPENAI_API_KEY=${OPENAI_API_KEY}

# Database path (mounted volume suggested)
ENV DB_PATH=/data/app.db

# Working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy rest of the files
COPY . /app/

# Set Flask app variable
ENV FLASK_APP=app.py

# Expose port 5001
EXPOSE 5001

# Create volume mountpoint for DB persistence
VOLUME ["/data"]

# Start the app with gunicorn on port 5001
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5001", "app:app"]
