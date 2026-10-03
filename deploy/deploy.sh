#!/usr/bin/env bash
# Copy the bot to the server and install it.
#   deploy/deploy.sh            (uses "Host tradebot" from ~/.ssh/config)
#   deploy/deploy.sh myserver
# It does NOT restart the bot; run: ssh tradebot sudo systemctl restart tradebot
set -euo pipefail
HOST="${1:-tradebot}"
cd "$(dirname "$0")/.."

echo "→ copying code to $HOST"
rsync -az --delete \
  --exclude .venv --exclude data/ --exclude .env --exclude .git \
  --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache \
  ./ "$HOST:tradebot/"

echo "→ copying .env (API keys) — readable only by the server user"
rsync -az --chmod=F600 .env "$HOST:tradebot/.env"

echo "→ installing packages and the service"
ssh "$HOST" 'cd ~/tradebot && ~/.local/bin/uv sync --frozen --no-dev -q \
  && sudo cp deploy/tradebot.service /etc/systemd/system/tradebot.service \
  && sudo systemctl daemon-reload && sudo systemctl enable tradebot -q'

echo "✓ deployed. (Re)start with:  ssh $HOST sudo systemctl restart tradebot"
