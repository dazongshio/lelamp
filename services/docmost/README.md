# LeLamp Docmost Wiki

This directory contains the self-hosted Docmost wiki service for LeLamp.

## Start

Install Docker first if it is not available:

```bash
/home/lemp/lelamp/services/docmost/install_docker_debian.sh
newgrp docker
```

Then start Docmost:

```bash
cd /home/lemp/lelamp/services/docmost
./start.sh
```

Open:

```text
http://localhost:3100
```

On LAN, replace `localhost` with the Raspberry Pi IP.

## Operations

```bash
docker compose ps || docker-compose ps
docker compose logs -f docmost || docker-compose logs -f docmost
docker compose pull || docker-compose pull
./start.sh
docker compose down || docker-compose down
```

Persistent data is stored in Docker volumes declared by `docker-compose.yml`.
