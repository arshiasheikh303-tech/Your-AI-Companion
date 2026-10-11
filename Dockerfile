FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-voice.txt ./
# voice (faster-whisper) is optional; remove the second line if you don't want it in the image
RUN pip install -r requirements.txt && pip install -r requirements-voice.txt
COPY . .
EXPOSE 8000
ENV API_HOST=0.0.0.0
CMD ["python", "run.py"]
