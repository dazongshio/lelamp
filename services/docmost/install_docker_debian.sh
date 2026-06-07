#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y docker.io docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

echo "Docker has been installed."
echo "Run this before starting Docmost in the current terminal:"
echo "  newgrp docker"
echo "Then run:"
echo "  cd /home/lemp/lelamp/services/docmost && ./start.sh"
