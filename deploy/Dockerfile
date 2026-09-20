FROM python:3.12-slim

# ffmpeg normalises Telegram's OGG/Opus; build tools are for whisper.cpp.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg git build-essential cmake ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# whisper.cpp, built from source so it matches the host architecture
# (arm64 on a Pi, amd64 on a dev machine).
RUN git clone --depth 1 https://github.com/ggml-org/whisper.cpp /tmp/whisper \
    && cmake -B /tmp/whisper/build -S /tmp/whisper -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/whisper/build --config Release -j "$(nproc)" \
    && cp /tmp/whisper/build/bin/whisper-cli /usr/local/bin/ \
    && rm -rf /tmp/whisper

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY backend/pyproject.toml /app/
RUN uv pip install --system --no-cache -r pyproject.toml

COPY backend/app /app/app

ENV WHISPER_CLI=/usr/local/bin/whisper-cli \
    WHISPER_MODEL_PATH=/app/models/ggml-small.bin \
    DATABASE_PATH=/app/data/calendar_agent.db

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
