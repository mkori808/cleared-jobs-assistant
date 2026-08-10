FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY src/ src/
COPY static/ static/
COPY config/ config/

# data/tracker.db lives here; mount a volume over it so it survives rebuilds
RUN mkdir -p data

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
