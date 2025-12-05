import os
import tempfile
import asyncio
import time
import uuid
import threading
import subprocess

from flask import Flask, render_template, request, jsonify, send_file
import edge_tts

app = Flask(__name__)

# ------------------------------------------
# VOICES
# ------------------------------------------
VOICE_PRESETS = [
    ("en-US-GuyNeural", "English (US) – Guy (Deep Male)"),
    ("en-US-JasonNeural", "English (US) – Jason (Cinematic Male)"),
    ("en-US-DavisNeural", "English (US) – Davis (Soft Male)"),

    ("en-US-AriaNeural", "English (US) – Aria (Emotional Female)"),
    ("en-US-JennyNeural", "English (US) – Jenny (Narrator Female)"),
    ("en-US-AnaNeural",  "English (US) – Ana (Calm Female)"),

    ("en-GB-RyanNeural",  "English (UK) – Ryan (Male)"),
    ("en-GB-MaisieNeural","English (UK) – Maisie (Female)"),

    ("en-AU-WilliamNeural","English (AU) – William (Male)"),
    ("en-AU-NatashaNeural","English (AU) – Natasha (Female)"),

    ("en-IN-RaviNeural",  "English (IN) – Ravi (Male)"),
    ("en-IN-NeerjaNeural","English (IN) – Neerja (Female)"),

    ("ur-PK-AsadNeural", "Urdu (PK) – Asad (Male)"),
    ("ur-PK-UzmaNeural", "Urdu (PK) – Uzma (Female)"),

    ("hi-IN-MadhurNeural", "Hindi (IN) – Madhur (Male)"),
    ("hi-IN-SwaraNeural",  "Hindi (IN) – Swara (Female)"),
]

# ------------------------------------------
# TTS PARAMS
# ------------------------------------------
def edge_tts_params(speed, tone):
    if speed == "Slow":
        rate = "-20%"
    elif speed == "Fast":
        rate = "+25%"
    else:
        rate = "+0%"

    pitch = "+0Hz"
    volume = "+0%"

    if tone == "Deep":
        pitch = "-50Hz"
    elif tone == "Soft":
        volume = "-15%"
    elif tone == "Deep Male":
        pitch = "-80Hz"
    elif tone == "Soft Female":
        pitch = "+20Hz"
        volume = "-10%"

    return rate, pitch, volume


async def generate_chunk(text, voice, rate, pitch, volume, out_file):
    communicate = edge_tts.Communicate(
        text=text, voice=voice, rate=rate, pitch=pitch, volume=volume
    )
    await communicate.save(out_file)


# ------------------------------------------
# JOB MANAGER
# ------------------------------------------
jobs = {}  # job_id → {progress, eta, cancel, done, error, file}


# ------------------------------------------
# HOME PAGE
# ------------------------------------------
@app.get("/")
def index():
    return render_template("index.html", voices=VOICE_PRESETS)


# ------------------------------------------
# START JOB
# ------------------------------------------
@app.post("/start-job")
def start_job():
    data = request.get_json()

    text = data.get("text", "").strip()
    voice = data.get("voice")
    speed = data.get("speed")
    tone = data.get("tone")

    if not text:
        return jsonify({"error": "EMPTY_TEXT"}), 400

    job_id = str(uuid.uuid4())

    jobs[job_id] = {
        "progress": 0,
        "eta": 0,
        "cancel": False,
        "done": False,
        "error": None,
        "file": None
    }

    threading.Thread(
        target=background_worker,
        args=(job_id, text, voice, speed, tone),
        daemon=True
    ).start()

    return jsonify({"job_id": job_id})


# ------------------------------------------
# BACKGROUND WORKER
# ------------------------------------------
def background_worker(job_id, text, voice, speed, tone):
    try:
        rate, pitch, volume = edge_tts_params(speed, tone)

        CHUNK_SIZE = 2000
        chunks = [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
        total_chunks = len(chunks)

        start_time = time.time()
        temp_files = []

        for i, chunk in enumerate(chunks):

            if jobs[job_id]["cancel"]:
                jobs[job_id]["error"] = "CANCELLED"
                return

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
            temp_files.append(tmp.name)
            tmp.close()

            asyncio.run(generate_chunk(chunk, voice, rate, pitch, volume, tmp.name))

            # Update progress
            progress = int(((i + 1) / total_chunks) * 100)
            elapsed = time.time() - start_time
            per_chunk = elapsed / (i + 1)
            eta = int((total_chunks - (i + 1)) * per_chunk)

            jobs[job_id]["progress"] = progress
            jobs[job_id]["eta"] = eta

        # MERGE ALL CHUNKS SAFELY
        list_file = "list_files.txt"
        with open(list_file, "w", encoding="utf8") as f:
            for p in temp_files:
                safe = p.replace("\\", "/")
                f.write(f"file '{safe}'\n")

        result_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name

        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", result_file],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        os.remove(list_file)
        for p in temp_files:
            os.remove(p)

        jobs[job_id]["done"] = True
        jobs[job_id]["file"] = result_file

    except Exception as e:
        jobs[job_id]["error"] = str(e)


# ------------------------------------------
# PROGRESS API
# ------------------------------------------
@app.get("/progress/<job_id>")
def progress(job_id):
    if job_id not in jobs:
        return jsonify({"error": "INVALID_JOB"}), 404
    return jsonify(jobs[job_id])


# ------------------------------------------
# CANCEL JOB
# ------------------------------------------
@app.post("/cancel/<job_id>")
def cancel(job_id):
    if job_id in jobs:
        jobs[job_id]["cancel"] = True
    return jsonify({"status": "OK"})

# ------------------------------------------
# SHORT PREVIEW (15–20 seconds)
# ------------------------------------------
@app.post("/preview")
def preview():
    data = request.get_json()
    text = data.get("text", "").strip()

    if not text:
        return "EMPTY TEXT", 400

    voice = data.get("voice")
    speed = data.get("speed")
    tone = data.get("tone")

    rate, pitch, volume = edge_tts_params(speed, tone)

    # Preview only first 300 characters (10–20 sec approx)
    preview_text = text[:300]

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    out_path = tmp.name
    tmp.close()

    try:
        asyncio.run(generate_chunk(preview_text, voice, rate, pitch, volume, out_path))
        return send_file(out_path, mimetype="audio/mpeg")
    except Exception as e:
        return f"Preview error: {e}", 500


# ------------------------------------------
# DOWNLOAD FINAL AUDIO
# ------------------------------------------
@app.get("/download/<job_id>")
def download(job_id):
    if job_id not in jobs or not jobs[job_id]["done"]:
        return "Not ready", 400

    return send_file(
        jobs[job_id]["file"],
        as_attachment=True,
        download_name="tts_output.mp3",
        mimetype="audio/mpeg"
    )


# ------------------------------------------
# RUN SERVER
# ------------------------------------------
if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


