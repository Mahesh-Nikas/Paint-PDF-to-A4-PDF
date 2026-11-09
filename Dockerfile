FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PORT=7860
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m appuser && chown -R appuser /app
USER appuser
EXPOSE 7860
# 1 worker is required: job status is kept in memory. Threads handle parallel requests.
CMD gunicorn -b 0.0.0.0:${PORT} -w 1 --threads 8 --timeout 120 app:app
