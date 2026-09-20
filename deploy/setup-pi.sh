#!/usr/bin/env bash
# One-shot Raspberry Pi setup: dependencies, whisper.cpp, and a model.
set -euo pipefail

MODEL="${1:-small}"   # small (recommended on a Pi 5) | base | medium
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> System packages"
sudo apt-get update
sudo apt-get install -y ffmpeg git build-essential cmake curl

if ! command -v uv >/dev/null 2>&1; then
    echo "==> uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

echo "==> whisper.cpp"
if [ ! -d "$HOME/whisper.cpp" ]; then
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$HOME/whisper.cpp"
fi
cmake -B "$HOME/whisper.cpp/build" -S "$HOME/whisper.cpp" -DCMAKE_BUILD_TYPE=Release
cmake --build "$HOME/whisper.cpp/build" --config Release -j "$(nproc)"

echo "==> Whisper model: $MODEL"
mkdir -p "$HERE/backend/models"
bash "$HOME/whisper.cpp/models/download-ggml-model.sh" "$MODEL"
cp "$HOME/whisper.cpp/models/ggml-$MODEL.bin" "$HERE/backend/models/"

echo "==> Python dependencies"
cd "$HERE/backend"
uv sync

cat <<NOTE

Done. Remaining steps:

  1. cp .env.example .env         and fill in the keys
     Set WHISPER_CLI=$HOME/whisper.cpp/build/bin/whisper-cli
     Set WHISPER_MODEL_PATH=$HERE/backend/models/ggml-$MODEL.bin
  2. cp config.toml.example config.toml
  3. Copy credentials.json and token.json across from the Mac
     (the Google consent flow needs a browser, so run it there first)
  4. sudo cp ../deploy/calendar-agent.service /etc/systemd/system/
     sudo systemctl enable --now calendar-agent

NOTE
