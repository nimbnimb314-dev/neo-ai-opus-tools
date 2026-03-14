from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / ".videomemo-data"
UPLOAD_DIR = DATA_DIR / "uploads"
JOB_DIR = DATA_DIR / "jobs"
MODEL_DIR = DATA_DIR / "models"
PROJECT_MODEL_PATH = MODEL_DIR / "ggml-base.bin"
DEFAULT_MODEL_PATH = os.environ.get("WHISPER_MODEL_PATH", "").strip() or (
    str(PROJECT_MODEL_PATH) if PROJECT_MODEL_PATH.exists() else ""
)
DEFAULT_LANGUAGE = os.environ.get("VIDEOMEMO_LANGUAGE", "ja").strip() or "ja"
GPU_AVAILABLE = shutil.which("nvidia-smi") is not None


def app_mode_enabled() -> bool:
    return os.environ.get("VIDEOMEMO_APP_MODE", "").strip() == "1"


def ensure_directories() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)


def format_timecode(seconds: float) -> str:
    safe = max(0, int(seconds))
    hours = safe // 3600
    minutes = (safe % 3600) // 60
    seconds = safe % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_srt_timestamp(raw: str) -> float:
    clock, millis = raw.split(",")
    hours, minutes, seconds = [int(part) for part in clock.split(":")]
    return hours * 3600 + minutes * 60 + seconds + int(millis) / 1000


def parse_srt(content: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    blocks = [block.strip() for block in content.replace("\r", "").split("\n\n") if block.strip()]

    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if len(lines) < 3:
            continue
        time_line = lines[1]
        if " --> " not in time_line:
            continue
        start_raw, end_raw = [part.strip() for part in time_line.split(" --> ", 1)]
        start = parse_srt_timestamp(start_raw)
        end = parse_srt_timestamp(end_raw)
        text = " ".join(lines[2:]).strip()
        entries.append(
            {
                "time": int(start),
                "start": start,
                "end": end,
                "label": format_timecode(start),
                "text": text,
            }
        )

    return entries


def ffmpeg_filter_escape(path: Path) -> str:
    value = path.resolve().as_posix()
    value = value.replace(":", r"\:")
    value = value.replace("'", r"\'")
    value = value.replace(",", r"\,")
    value = value.replace("[", r"\[")
    value = value.replace("]", r"\]")
    return value


@dataclass
class Job:
    id: str
    filename: str
    upload_path: Path
    model_path: str
    language: str
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    error: str = ""
    transcript_path: str = ""
    transcript_entries: list[dict[str, Any]] = field(default_factory=list)
    progress_percent: int = 0
    progress_stage: str = "待機中"
    duration_seconds: float = 0.0
    backend: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "transcript_path": self.transcript_path,
            "transcript_count": len(self.transcript_entries),
            "progress_percent": self.progress_percent,
            "progress_stage": self.progress_stage,
            "duration_seconds": self.duration_seconds,
            "backend": self.backend,
        }


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
APP_LAST_PING: float | None = None
APP_PING_LOCK = threading.Lock()


def update_job(job_id: str, **kwargs: Any) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        for key, value in kwargs.items():
            setattr(job, key, value)
        job.updated_at = datetime.now().isoformat(timespec="seconds")


def current_job_progress(job_id: str) -> int:
    with JOBS_LOCK:
        return JOBS[job_id].progress_percent


def record_app_ping() -> None:
    global APP_LAST_PING
    with APP_PING_LOCK:
        APP_LAST_PING = datetime.now().timestamp()


def seconds_since_app_ping() -> float | None:
    with APP_PING_LOCK:
        if APP_LAST_PING is None:
            return None
        return max(0.0, datetime.now().timestamp() - APP_LAST_PING)


def probe_media_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return 0.0

    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, cwd=BASE_DIR)
    except OSError:
        return 0.0

    if result.returncode != 0:
        return 0.0

    try:
        return max(0.0, float((result.stdout or "").strip()))
    except ValueError:
        return 0.0


def build_filter_graph(model_path: Path, language: str, destination: Path, use_gpu: bool) -> str:
    return (
        "whisper="
        f"model='{ffmpeg_filter_escape(model_path)}':"
        f"language={language}:"
        f"use_gpu={'true' if use_gpu else 'false'}:"
        "format=srt:"
        f"destination='{ffmpeg_filter_escape(destination)}'"
    )


def build_audio_filter_chain(model_path: Path, language: str, destination: Path, use_gpu: bool, cleanup_audio: bool) -> str:
    whisper_filter = build_filter_graph(model_path, language, destination, use_gpu)
    if not cleanup_audio:
        return whisper_filter
    return ",".join(
        [
            "highpass=f=120",
            "lowpass=f=3200",
            "afftdn=nf=-20",
            "speechnorm=e=6.25:r=0.0001:l=1",
            whisper_filter,
        ]
    )


def build_backend_label(use_gpu: bool, cleanup_audio: bool) -> str:
    device = "GPU" if use_gpu else "CPU"
    suffix = " + cleanup" if cleanup_audio else ""
    return f"ffmpeg whisper.cpp ({device}{suffix})"


def normalize_transcript_text(text: str) -> str:
    lowered = text.strip().lower()
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip(" .,!?-_\"'[]()")


def is_low_confidence_transcript(entries: list[dict[str, Any]], language: str) -> tuple[bool, str]:
    raw_texts = [entry["text"].strip() for entry in entries if entry["text"].strip()]
    texts = [normalize_transcript_text(text) for text in raw_texts]
    if not texts:
        return True, "音声区間を判定できませんでした。"

    counts = Counter(texts)
    dominant_ratio = max(counts.values()) / len(texts)
    unique_ratio = len(counts) / len(texts)
    bracket_ratio = sum(text.startswith("[") or text.startswith("(") for text in raw_texts) / len(raw_texts)
    url_ratio = sum("www." in text.lower() or ".com" in text.lower() or ".co." in text.lower() for text in raw_texts) / len(raw_texts)

    if len(texts) >= 5 and dominant_ratio >= 0.6:
        return True, "同じ文が繰り返されており、認識の信頼性が低いです。"
    if len(texts) >= 5 and unique_ratio <= 0.35:
        return True, "認識結果のバリエーションが少なすぎます。"
    if len(texts) >= 5 and (bracket_ratio >= 0.7 or url_ratio >= 0.3):
        return True, "環境音やノイズを言葉として誤認している可能性が高いです。"

    if language == "ja":
        japanese_chars = sum(
            1
            for text in texts
            for char in text
            if "\u3040" <= char <= "\u30ff" or "\u4e00" <= char <= "\u9fff"
        )
        letter_chars = sum(1 for text in texts for char in text if char.isalpha() or char.isdigit())
        if letter_chars >= 20 and japanese_chars / max(letter_chars, 1) < 0.15:
            return True, "日本語として不自然な文字列が多く、信頼性が低いです。"

    return False, ""


def run_ffmpeg_transcription(
    job_id: str,
    job: Job,
    srt_path: Path,
    duration_seconds: float,
    *,
    use_gpu: bool,
    cleanup_audio: bool,
) -> tuple[bool, str]:
    filter_graph = build_audio_filter_chain(Path(job.model_path), job.language, srt_path, use_gpu, cleanup_audio)
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-i",
        str(job.upload_path),
        "-vn",
        "-af",
        filter_graph,
        "-f",
        "null",
        "-",
    ]

    if cleanup_audio:
        stage_label = "音声補正付きで再解析中"
    else:
        stage_label = "GPUで文字起こし中" if use_gpu else "CPUで文字起こし中"
    update_job(
        job_id,
        status="processing",
        error="",
        progress_stage=stage_label,
        progress_percent=max(3, current_job_progress(job_id)),
        duration_seconds=duration_seconds,
        backend=build_backend_label(use_gpu, cleanup_audio),
    )

    try:
        process = subprocess.Popen(
            command,
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as error:
        return False, str(error)

    out_seconds = 0.0
    error_lines: list[str] = []

    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line:
            continue
        if "=" not in line:
            error_lines.append(line)
            continue

        key, value = line.split("=", 1)
        if key == "out_time_ms":
            try:
                out_seconds = max(0.0, int(value) / 1_000_000)
            except ValueError:
                continue
            if duration_seconds > 0:
                percent = min(99, max(3, int((out_seconds / duration_seconds) * 100)))
                update_job(job_id, progress_percent=percent, progress_stage=stage_label)
        elif key == "progress" and value == "end":
            update_job(job_id, progress_percent=99, progress_stage="最終整形中")

    returncode = process.wait()
    if returncode != 0:
        message = "\n".join(error_lines).strip() or "ffmpeg failed"
        return False, message
    return True, ""


def run_transcription(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]

    model_path = Path(job.model_path)
    if not model_path.exists():
        update_job(job_id, status="error", error=f"Whisper model not found: {model_path}")
        return

    out_dir = JOB_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    srt_path = out_dir / "transcript.srt"
    duration_seconds = probe_media_duration(job.upload_path)
    update_job(
        job_id,
        status="processing",
        error="",
        progress_percent=1,
        progress_stage="準備中",
        duration_seconds=duration_seconds,
        backend="ffmpeg whisper.cpp",
    )

    if srt_path.exists():
        srt_path.unlink()

    success, message = run_ffmpeg_transcription(
        job_id,
        job,
        srt_path,
        duration_seconds,
        use_gpu=GPU_AVAILABLE,
        cleanup_audio=False,
    )
    if not success and GPU_AVAILABLE:
        update_job(job_id, progress_stage="GPU失敗。CPUへ切替中", progress_percent=max(5, current_job_progress(job_id)))
        if srt_path.exists():
            srt_path.unlink()
        success, message = run_ffmpeg_transcription(
            job_id,
            job,
            srt_path,
            duration_seconds,
            use_gpu=False,
            cleanup_audio=False,
        )

    if not success:
        update_job(job_id, status="error", error=message, progress_stage="失敗")
        return

    if not srt_path.exists():
        update_job(job_id, status="error", error="ffmpeg completed but transcript file was not created.")
        return

    content = srt_path.read_text(encoding="utf-8", errors="replace")
    entries = parse_srt(content)
    low_confidence, reason = is_low_confidence_transcript(entries, job.language)
    if low_confidence:
        update_job(job_id, progress_stage="低信頼のため音声補正で再試行中", progress_percent=max(60, current_job_progress(job_id)))
        if srt_path.exists():
            srt_path.unlink()
        retry_success, retry_message = run_ffmpeg_transcription(
            job_id,
            job,
            srt_path,
            duration_seconds,
            use_gpu=GPU_AVAILABLE,
            cleanup_audio=True,
        )
        if not retry_success and GPU_AVAILABLE:
            update_job(job_id, progress_stage="補正GPU失敗。CPUへ切替中", progress_percent=max(65, current_job_progress(job_id)))
            if srt_path.exists():
                srt_path.unlink()
            retry_success, retry_message = run_ffmpeg_transcription(
                job_id,
                job,
                srt_path,
                duration_seconds,
                use_gpu=False,
                cleanup_audio=True,
            )
        if not retry_success:
            update_job(job_id, status="error", error=retry_message, progress_stage="失敗")
            return

        content = srt_path.read_text(encoding="utf-8", errors="replace")
        entries = parse_srt(content)
        low_confidence, reason = is_low_confidence_transcript(entries, job.language)
        if low_confidence:
            update_job(
                job_id,
                status="error",
                error=f"{reason} 元動画(mp4)か、声がはっきり入った音声で再試行してください。",
                progress_stage="低信頼で停止",
            )
            return

    update_job(
        job_id,
        status="completed",
        transcript_path=str(srt_path),
        transcript_entries=entries,
        error="",
        progress_percent=100,
        progress_stage="完了",
    )


class VideoMemoHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        # `pythonw` has no stderr/stdout console. Default logging can break responses there.
        return

    def send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config":
            self.send_json(
                {
                    "ok": True,
                    "default_model_path": DEFAULT_MODEL_PATH,
                    "default_language": DEFAULT_LANGUAGE,
                    "ffmpeg_available": shutil.which("ffmpeg") is not None,
                    "gpu_available": GPU_AVAILABLE,
                    "backend_name": "ffmpeg whisper.cpp",
                    "app_mode": app_mode_enabled(),
                }
            )
            return

        if path.startswith("/api/status/"):
            job_id = path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self.send_json({"ok": False, "error": "Job not found."}, HTTPStatus.NOT_FOUND)
                return
            self.send_json({"ok": True, "job": job.to_dict()})
            return

        if path.startswith("/api/transcript/"):
            job_id = path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self.send_json({"ok": False, "error": "Job not found."}, HTTPStatus.NOT_FOUND)
                return
            if job.status != "completed":
                self.send_json({"ok": False, "error": "Transcript is not ready yet."}, HTTPStatus.CONFLICT)
                return
            transcript_text = "\n".join(
                f"{format_timecode(entry['time'])} {entry['text']}" for entry in job.transcript_entries
            )
            self.send_json(
                {
                    "ok": True,
                    "entries": job.transcript_entries,
                    "transcript_text": transcript_text,
                }
            )
            return

        if path == "/api/ping":
            record_app_ping()
            self.send_json({"ok": True})
            return

        if path == "/":
            self.path = "/index.html"

        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == "/api/ping":
            record_app_ping()
            self.send_json({"ok": True})
            return

        if parsed.path != "/api/upload":
            self.send_json({"ok": False, "error": "Unknown endpoint."}, HTTPStatus.NOT_FOUND)
            return

        if shutil.which("ffmpeg") is None:
            self.send_json({"ok": False, "error": "ffmpeg is not installed."}, HTTPStatus.BAD_REQUEST)
            return

        filename = self.headers.get("X-Filename", "").strip() or "upload.bin"
        model_path = self.headers.get("X-Model-Path", "").strip() or DEFAULT_MODEL_PATH
        language = self.headers.get("X-Language", "").strip() or DEFAULT_LANGUAGE
        content_length = int(self.headers.get("Content-Length", "0"))

        if content_length <= 0:
            self.send_json({"ok": False, "error": "Empty request body."}, HTTPStatus.BAD_REQUEST)
            return

        if not model_path:
            self.send_json(
                {
                    "ok": False,
                    "error": "Whisper model path is required. Set WHISPER_MODEL_PATH or enter it in the UI.",
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        safe_name = Path(filename).name
        job_id = uuid.uuid4().hex
        upload_path = UPLOAD_DIR / f"{job_id}-{safe_name}"

        remaining = content_length
        with upload_path.open("wb") as handle:
            while remaining > 0:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                handle.write(chunk)
                remaining -= len(chunk)

        job = Job(
            id=job_id,
            filename=safe_name,
            upload_path=upload_path,
            model_path=model_path,
            language=language,
            duration_seconds=probe_media_duration(upload_path),
            backend="ffmpeg whisper.cpp",
        )

        with JOBS_LOCK:
            JOBS[job_id] = job

        thread = threading.Thread(target=run_transcription, args=(job_id,), daemon=True)
        thread.start()

        self.send_json({"ok": True, "job": job.to_dict()})


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def build_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    ensure_directories()
    return ReusableThreadingHTTPServer((host, port), VideoMemoHandler)


def main() -> None:
    server = build_server()
    host, port = server.server_address
    print(f"VideoMemo server running at http://{host}:{port}")
    print("Set WHISPER_MODEL_PATH to a local whisper.cpp model file if you want a default model.")
    server.serve_forever()


if __name__ == "__main__":
    main()
