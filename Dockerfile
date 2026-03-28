FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Build React frontend
COPY frontend/package*.json frontend/
RUN cd frontend && npm ci

COPY frontend/ frontend/
RUN cd frontend && npm run build

# Copy the rest of the app
COPY . .

EXPOSE 8000

# Default: API server. Worker overrides CMD in ACA config.
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
