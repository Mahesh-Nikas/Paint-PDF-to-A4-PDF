import os
import shutil
import tempfile
import threading
import time
import uuid

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from converter import ConvertError, Options, convert

# ---- Limits (override with environment variables on your host) ----
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", 25))
MAX_PAGES = int(os.environ.get("MAX_PAGES", 120))
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL", 1))   # conversions at the same time
JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_SECONDS", 1800))  # delete files after 30 min
SOURCE_URL = os.environ.get("SOURCE_URL", "")            # optional "source code" link

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

WORK_DIR = tempfile.mkdtemp(prefix="paint2a4_")
jobs = {}
jobs_lock = threading.Lock()
slots = threading.Semaphore(MAX_PARALLEL)


def _set(job_id, **kw):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(kw)


def _int(name, default, lo, hi):
    try:
        return max(lo, min(hi, int(request.form.get(name, default))))
    except (TypeError, ValueError):
        return default


def _run(job_id, in_path, out_path, opts):
    _set(job_id, status="queued")
    with slots:                                   # protects RAM on small servers
        _set(job_id, status="working")
        try:
            pages = convert(
                in_path, out_path, opts, max_pages=MAX_PAGES,
                progress=lambda d, t: _set(job_id, done=d, total=t),
            )
            _set(job_id, status="done", pages=pages)
        except ConvertError as e:
            _set(job_id, status="error", error=str(e))
        except MemoryError:
            _set(job_id, status="error",
                 error="The server ran out of memory. Try a smaller PDF or lower quality.")
        except Exception:
            app.logger.exception("conversion failed")
            _set(job_id, status="error", error="Something went wrong while converting this file.")
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)                # never keep the user's upload


def _cleanup_loop():
    while True:
        time.sleep(60)
        now = time.time()
        with jobs_lock:
            old = [j for j, v in jobs.items() if now - v["created"] > JOB_TTL_SECONDS]
            for j in old:
                jobs.pop(j, None)
        for j in old:
            shutil.rmtree(os.path.join(WORK_DIR, j), ignore_errors=True)


threading.Thread(target=_cleanup_loop, daemon=True).start()


@app.get("/")
def index():
    return render_template("index.html", max_mb=MAX_UPLOAD_MB, max_pages=MAX_PAGES,
                           source_url=SOURCE_URL)


@app.get("/health")
def health():
    return "ok"


@app.post("/api/convert")
def api_convert():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="Choose a PDF file first."), 400

    head = f.stream.read(5)
    f.stream.seek(0)
    if head != b"%PDF-":
        return jsonify(error="That file is not a PDF."), 400

    opts = Options(
        dpi=150 if request.form.get("quality") == "standard" else 200,
        left_margin=_int("left_margin", 120, 0, 400),
        header_text=request.form.get("header_text", "").strip()[:80],
        page_numbers=request.form.get("page_numbers") == "on",
        keep_diagrams=request.form.get("keep_diagrams") == "on",
    )

    job_id = uuid.uuid4().hex
    folder = os.path.join(WORK_DIR, job_id)
    os.makedirs(folder)
    in_path = os.path.join(folder, "input.pdf")
    out_path = os.path.join(folder, "output.pdf")
    f.save(in_path)

    stem = os.path.splitext(secure_filename(f.filename))[0] or "document"
    with jobs_lock:
        jobs[job_id] = {"status": "queued", "done": 0, "total": 0, "created": time.time(),
                        "name": f"{stem}_A4.pdf", "path": out_path}

    threading.Thread(target=_run, args=(job_id, in_path, out_path, opts), daemon=True).start()
    return jsonify(job_id=job_id)


@app.get("/api/status/<job_id>")
def api_status(job_id):
    with jobs_lock:
        j = jobs.get(job_id)
        if not j:
            return jsonify(status="expired"), 404
        return jsonify({k: j.get(k) for k in ("status", "done", "total", "pages", "error")})


@app.get("/api/download/<job_id>")
def api_download(job_id):
    with jobs_lock:
        j = jobs.get(job_id)
        ready = j and j["status"] == "done" and os.path.exists(j["path"])
        path, name = (j["path"], j["name"]) if ready else (None, None)
    if not ready:
        return "File not ready or expired. Please convert again.", 404
    return send_file(path, as_attachment=True, download_name=name, mimetype="application/pdf")


@app.errorhandler(413)
def too_big(_):
    return jsonify(error=f"File is too large. The limit is {MAX_UPLOAD_MB} MB."), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
