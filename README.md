# whisper-api-stt

docker build -t flask-whisper-app .   

docker run -d -p 5001:5000 flask-whisper-app

docker build --build-arg OPENAI_API_KEY=your_key -t flask-whisper-app
