# Immagine per il deploy su Render (o qualunque host container-based).
# Nota: la cartella ffmpeg/ (binari Windows) NON viene copiata qui — su
# Linux usiamo ffmpeg installato via apt, gia' gestito automaticamente da
# backend/downloader.py (ffmpeg_location() ripiega sul PATH di sistema).

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

ENV YTGRABBER_MODE=web
EXPOSE 8756

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8756"]
