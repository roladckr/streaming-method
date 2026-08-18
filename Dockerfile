FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV PORT=8080

# Production target is Cloud Run + Google Drive. The image ships no
# data/ directory (company spreadsheets are never baked into the
# image), so FILE_SOURCE=local would break /process at runtime -
# LocalFileSource would look for /app/data, which doesn't exist here.
# Override with `-e FILE_SOURCE=local` (plus a mounted data/ dir) for
# local container testing only.
ENV FILE_SOURCE=google_drive

CMD exec gunicorn \
    --bind :$PORT \
    --workers 1 \
    --threads 4 \
    --timeout 0 \
    src.app:app