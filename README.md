# Flirc MQTT Bridge

This project recreates the functionality of the `breeily/flirc_mqtt` container and expands it with:

- A FastAPI web server to manage IR patterns (create, update, delete).
- A SQLite database for persisting patterns in a structure compatible with `patterns.json`.
- REST endpoints for sending and receiving patterns using `irtools`.
- MQTT discovery and command handling so patterns appear as buttons in Home Assistant, while still allowing custom pattern payloads over MQTT.
- A ready-to-run Docker container (Linux) and native execution support on Windows.

## Features

- **Pattern management UI** – visit `/` to view and manage stored patterns.
- **REST API**:
  - `GET /api/patterns.json` – export all patterns in the original JSON structure.
  - `POST /api/patterns` – add or update patterns.
  - `PUT /api/patterns/{device}/{action}` – replace an existing pattern.
  - `DELETE /api/patterns/{device}/{action}` – remove a pattern.
  - `POST /api/send` – transmit a stored or custom pattern via `irtools`.
  - `POST /api/receive` – capture a pattern using `irtools listen` (optionally save it).
- **MQTT integration** – publishes Home Assistant discovery buttons for every stored pattern and listens for commands on:
  - `flirc_mqtt/commands/<device_action_format>` – triggers stored patterns.
  - `flirc_mqtt/send` – accepts custom payloads (`{"format": "...", "data": [...]}`).

## Prerequisites

- Python 3.12+
- MQTT broker credentials.
- **Flirc tools** (auto-installed on first run):
  - **Windows**: `start.ps1` will run `install_flirc_tools.ps1` if `flirc_util.exe` is missing.
  - **Linux/Docker**: `install_flirc_tools.sh` runs during Docker build or can be invoked manually.
  - **Manual install**: See [Flirc Linux Downloads](https://flirc.tv/pages/flirc-usb-linux-downloads) or run `curl apt.flirc.tv/install.sh | sudo bash` on Debian/Ubuntu.

## Configuration

Environment variables (defaults shown):

| Variable                | Default             | Description                        |
| ----------------------- | ------------------- | ---------------------------------- |
| `FLIRC_DB_PATH`         | `flirc_patterns.db` | SQLite database location           |
| `IRTOOLS_PATH`          | `irtools`           | Executable used for IR send/listen |
| `MQTT_BROKER`           | `localhost`         | MQTT broker address                |
| `MQTT_PORT`             | `1883`              | MQTT broker port                   |
| `MQTT_USERNAME`         | _(unset)_           | MQTT username                      |
| `MQTT_PASSWORD`         | _(unset)_           | MQTT password                      |
| `MQTT_CLIENT_ID`        | `flirc-mqtt`        | MQTT client identifier             |
| `MQTT_BASE_TOPIC`       | `flirc_mqtt`        | Base MQTT topic                    |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant`     | Discovery prefix                   |
| `MQTT_RETAIN`           | `true`              | Retain discovery messages          |
| `WEB_HOST`              | `0.0.0.0`           | Web server bind address            |
| `WEB_PORT`              | `8000`              | Web server port                    |
| `WEB_RELOAD`            | `false`             | Enable auto-reload (development)   |
| `IR_LISTEN_TIMEOUT`     | `10`                | Default listen timeout             |

## Running locally

### Windows (PowerShell)

```powershell
cd flirc-mqtt

# Create .env file with your MQTT credentials
@"
MQTT_BROKER=192.168.2.4
MQTT_PORT=1883
MQTT_USERNAME=homeassistant
MQTT_PASSWORD=super_secret
"@ | Out-File -Encoding utf8 .env

# Run in foreground (Ctrl+C to stop)
.\start.ps1

# OR run detached (background)
.\start.ps1 -Detached
```

The `start.ps1` script will:
- Auto-install Flirc tools if missing (downloads and runs `Flirc-Setup-3.27.19.exe` silently)
- Create/activate `.venv`
- Install Python dependencies
- Launch the bridge

### Linux

```bash
cd flirc-mqtt

# Install Flirc tools (if not already installed)
sudo ./install_flirc_tools.sh

# Create .env file
cat > .env <<EOF
MQTT_BROKER=192.168.2.4
MQTT_PORT=1883
MQTT_USERNAME=homeassistant
MQTT_PASSWORD=super_secret
EOF

# Setup and run
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run_flirc_mqtt.py
```

Visit `http://localhost:8000` for the management interface.

## Docker

```bash
docker build -t bluscream/flirc-mqtt .
docker run --rm \
  -e MQTT_BROKER=192.168.1.10 \
  -e MQTT_USERNAME=homeassistant \
  -e MQTT_PASSWORD=secret \
  -v $(pwd)/flirc_patterns.db:/app/flirc_patterns.db \
  -p 8000:8000 \
  bluscream/flirc-mqtt
```

Mount additional devices (e.g., `--device /dev/input/...`) if required by `irtools`.

## REST examples

### Add a pattern

```bash
curl -X POST http://localhost:8000/api/patterns \
  -H "Content-Type: application/json" \
  -d '{
    "device": "Compact Space Heater",
    "action": "Toggle",
    "formats": [
      {"format": "raw", "data": ["+9094 -4399 ..."]},
      {"format": "pronto", "data": ["0000 006D ..."]}
    ]
  }'
```

### Send a custom pattern

```bash
curl -X POST http://localhost:8000/api/send \
  -H "Content-Type: application/json" \
  -d '{
    "format": "raw",
    "data": ["+9094 -4399 ..."],
    "repeat": 1,
    "carrier": 38000
  }'
```

### Receive and save a pattern

```bash
curl -X POST http://localhost:8000/api/receive \
  -H "Content-Type: application/json" \
  -d '{
    "save": true,
    "device": "Test Device",
    "action": "Test Action",
    "format": "raw",
    "timeout": 15
  }'
```

This will listen with `irtools`, store the result in SQLite, and immediately expose it via MQTT.

## MQTT custom payload

Publish to `flirc_mqtt/send`:

```json
{
  "format": "raw",
  "data": ["+9094 -4399 ..."],
  "carrier": 38000,
  "repeat": 1
}
```

## Notes

- Home Assistant discovery topics are retained so buttons reappear after restart.
- When patterns are deleted, their discovery topics are cleared.
- The project currently assumes `irtools` supports `send --format ... --data ...` and `listen --format ...`. Adjust the command flags in `flirc_mqtt_app/irtools.py` if your version differs.
