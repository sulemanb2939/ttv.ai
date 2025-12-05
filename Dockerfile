# Python base image
FROM python:3.11-slim

# Env vars for Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# System dependencies (FFmpeg yahan install ho raha hai)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Workdir set karein
WORKDIR /app

# Dependencies install karein
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Baqi project files copy
COPY . .

# (Optional) Documented port (Render khud PORT env set karega)
EXPOSE 5000

# Start command
CMD ["python", "app.py"]
