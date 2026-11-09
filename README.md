---
title: Paint PDF to A4
emoji: 📄
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# Paint PDF to A4 PDF

Upload a PDF made from Paint screenshots. Get clean A4 pages with no wasted blank space,
no clipped text, and no diagram split across two pages.

**Cost: free.** No paid API, no API key, no external service. Everything runs on your own
server with open-source libraries (pypdfium2, numpy, Pillow, reportlab, Flask).

## Files

| File | Purpose |
|---|---|
| `converter.py` | Your conversion logic, cleaned up and made faster |
| `app.py` | Web server: upload, progress, download, limits, auto-cleanup |
| `templates/index.html` | The upload page |
| `Dockerfile` | For Hugging Face Spaces (and any Docker host) |
| `Procfile`, `requirements.txt` | For Render and similar hosts |

## 1. Run on your computer (Windows)

Install Python 3.10 or newer from python.org (tick "Add Python to PATH"). Then in the project folder:

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000 in your browser, upload a PDF, and download the result.
Stop the server with Ctrl+C. (On Mac/Linux use `source venv/bin/activate`.)

## 2. Deploy for free (recommended: Hugging Face Spaces)

Free Spaces give 2 vCPU and 16 GB RAM, which is the safest free option for this tool.

1. Create an account at huggingface.co, then **New Space**.
2. Name it, choose **Docker** as the SDK, keep hardware as **CPU basic (free)**, set it **Public**.
3. Upload all files from this folder (drag and drop in the Files tab, or use git). Keep the
   `---` block at the top of `README.md`: Spaces reads it.
4. Wait for the build (2 to 4 minutes). Your app appears at `https://<user>-<space-name>.hf.space`.

## 2b. Alternative: Render (free web service)

1. Push this folder to a GitHub repository.
2. On render.com: **New > Web Service**, pick the repo, runtime **Python**.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn -b 0.0.0.0:$PORT -w 1 --threads 8 --timeout 120 app:app`
5. Environment: `PYTHON_VERSION=3.12.3`, `MAX_UPLOAD_MB=10`, `MAX_PAGES=40`, `MAX_PIXELS_PER_PAGE=8000000`

Render's free instance has only 512 MB RAM, so keep those limits low.

## 3. Problems that affect free hosting (and what this project already does)

| Issue | What to know |
|---|---|
| **Vercel / Netlify / GitHub Pages / Cloudflare Pages** | Do **not** use. Serverless functions have small upload limits and short time limits, and they cannot run the background conversion this tool needs. Static hosts cannot run Python at all. |
| **Low RAM (Render free: 512 MB)** | Large pages use a lot of memory. The converter lowers DPI automatically for huge pages (`MAX_PIXELS_PER_PAGE`), and `MAX_PARALLEL=1` runs one conversion at a time. On Hugging Face you can raise these. |
| **Request time-outs** | Conversion runs in a background thread and the page polls progress, so a long file never hits a host's request time-out. |
| **Sleeping apps** | Render free sleeps after 15 minutes idle (about 1 minute to wake). Hugging Face free sleeps after 48 hours idle. The page shows a clear message if the first request fails. |
| **No permanent disk** | Files live in a temp folder. Uploads are deleted right after conversion and results after 30 minutes (`JOB_TTL_SECONDS`). Nothing is stored long term. |
| **Single worker** | Job progress is kept in memory, so the server must run **one** gunicorn worker (`-w 1`) with several threads. Do not raise `-w`, or progress and downloads will randomly 404. If you ever need more users, move job state to Redis. |
| **Abuse** | Limits: 25 MB upload, 120 pages, PDF-only check. For a public site, consider lowering them. There is no login or rate limit per user. |
| **Licensing** | Your original code used PyMuPDF, which is AGPL (public web services must publish their source). This version uses pypdfium2 (Apache/BSD), so there is no such requirement. |
| **Performance** | Rendering and image work use CPU. Free hosts are slow, so expect several seconds per page on Render free and faster on Hugging Face. |
| **Output size** | Pages are kept as sharp images, so files are large (about 0.3 to 0.8 MB per page). "Standard" quality makes smaller, faster files. |

## Settings (environment variables, all optional)

| Name | Default | Meaning |
|---|---|---|
| `MAX_UPLOAD_MB` | 25 | Largest upload |
| `MAX_PAGES` | 120 | Most source pages |
| `MAX_PARALLEL` | 1 | Conversions at once |
| `MAX_PIXELS_PER_PAGE` | 12000000 | Memory guard per rendered page |
| `JOB_TTL_SECONDS` | 1800 | How long results are kept |
| `SOURCE_URL` | empty | Adds a "Source code" link in the footer |

## Tuning the result

Gap and diagram behaviour is in `Cfg` inside `converter.py` (values are for 200 DPI):
`max_gap` (largest blank gap kept), `diagram_min_h` (height that counts as a diagram),
`diagram_merge_gap`, `label_reach`, `pad`.

## Limits of this tool

- A diagram taller than one A4 page still has to be split.
- Moving a diagram to the next page leaves blank space at the bottom of the previous page.
- A diagram already cut between two source pages cannot be rejoined.
- Header text supports English letters only (built-in PDF font).

python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py