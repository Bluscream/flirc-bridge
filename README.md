# Flirc Bridge

This project recreates the functionality of the `breeily/flirc_bridge` container and expands it with:

- A FastAPI web server to manage IR patterns (create, update, delete).
- A SQLite database for persisting patterns in a structure compatible with `patterns.json`.
- REST endpoints for sending and receiving patterns using `irtools`.
- MQTT discovery and command handling so patterns appear as buttons in Home Assistant, while still allowing custom pattern payloads over MQTT.
- A ready-to-run Docker container (Linux) and native execution support on Windows.

## Screenshots

| Main Page | /manage | /status |
|----------|----------|----------|
| ![](https://files.catbox.moe/a1h1sm.png) | ![](https://files.catbox.moe/1vd3oq.png)  | ![](https://files.catbox.moe/pkz176.png) |


## Features

- **Pattern management UI** – visit `/` to view and manage stored devices, actions, and individual patterns (with Send/Edit/Delete controls).
- **REST API** (all endpoints accept query-string or JSON body parameters; body values take precedence):
  - `GET /api/status` – runtime status, tool versions, and configuration summary.
  - `GET /api/patterns.json` – export all devices/actions/patterns as JSON.
  - `POST /api/devices`, `PUT /api/devices/{device_id}`, `DELETE /api/devices/{device_id}` – manage devices.
  - `POST /api/actions`, `PUT /api/actions/{action_id}`, `DELETE /api/actions/{action_id}` – manage actions (linked to devices).
  - `POST /api/patterns`, `PUT /api/patterns/{pattern_id}`, `DELETE /api/patterns/{pattern_id}` – manage individual patterns.
  - `POST /api/send` (also responds to GET/PUT/PATCH/DELETE) – transmit stored patterns (by action/pattern UUID) or custom payloads. Set `save=1` to store custom payloads.
  - `POST /api/receive` – capture a pattern using `irtools listen` (optionally save it).
- **MQTT integration** – publishes Home Assistant discovery buttons for every action and, when pressed, sends all patterns assigned to that action (in order). Also listens for custom payloads on:
  - `${MQTT_BASE_TOPIC}/commands/...` – triggers stored actions.
  - `${MQTT_BASE_TOPIC}/send` – accepts custom payloads (`{"format": "...", "data": [...], "repeat": 1, "ik": 23000}`).

## Data model

Patterns are stored across three tables:

- **devices** – `id (UUID)`, `name`, `description`, `created_at`, `updated_at`
- **actions** – `id (UUID)`, `device_id`, `name`, `description`, `created_at`, `updated_at`
- **patterns** – `id (UUID)`, `action_id`, `format (raw|csv|pronto)`, `data` (JSON array of strings), `repeat`, `ik`, `hash` (md5 of format+data+repeat+ik), `created_at`, `updated_at`, `sent_at`

Every action may hold multiple patterns; when an action is triggered (via web, API, or MQTT) all of its patterns are transmitted in database order. If only a single pattern should be used, reference it directly by UUID.

## Module layout

- `flirc_bridge/application.py` – shared runtime that initializes logging, database, tools, and MQTT.
- `flirc_bridge/mqtt/` – MQTT discovery/command manager and cleanup utilities.
- `flirc_bridge/web/` – FastAPI application plus static assets and templates.

## Prerequisites

- Python 3.12+
- MQTT broker credentials.
- **Flirc tools** (auto-installed on first run):
  - **Windows**: `start.ps1` will run `install_flirc_tools.ps1` if `flirc_util.exe` is missing.
  - **Linux/Docker**: `install_flirc_tools.sh` runs during Docker build or can be invoked manually.
  - **Manual install**: See [Flirc Linux Downloads](https://flirc.tv/pages/flirc-usb-linux-downloads) or run `curl apt.flirc.tv/install.sh | sudo bash` on Debian/Ubuntu.

## Configuration

Environment variables (defaults shown):

| Variable                | Default             | Description                                                    |
| ----------------------- | ------------------- | -------------------------------------------------------------- |
| `DB_URI`                | `sqlite:///patterns.db` | SQLAlchemy connection string (e.g., `sqlite:///patterns.db` or `postgresql://user:pass@host:5432/db`) |
| `IRTOOLS_PATH`          | `irtools`           | Executable used for IR send/listen                             |
| `MQTT_BROKER`           | `localhost`         | MQTT broker address                                            |
| `MQTT_PORT`             | `1883`              | MQTT broker port                                               |
| `MQTT_USERNAME`         | `homeassistant`     | MQTT username                                                  |
| `MQTT_PASSWORD`         | _(unset)_           | MQTT password                                                  |
| `MQTT_PREFIX`           | `flirc_bridge`      | MQTT topic prefix and discovery identifier                     |
| `INSTANCE_NAME`         | `Flirc MQTT Bridge` | Used as MQTT device name and website title |
| `MQTT_BASE_TOPIC`       | `flirc_bridge`      | Base MQTT topic                                                |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant`     | Discovery prefix                                               |
| `MQTT_RETAIN`           | `true`              | Retain discovery messages                                      |
| `MQTT_ENABLED`          | `true`              | Disable to run the web UI without MQTT                         |
| `AUTO_STORE_PATTERNS`   | `false`             | Automatically persist received patterns                        |
| `WEB_ENABLED`           | `true`              | Disable to run headless (MQTT-only) mode                       |
| `WEB_TOKEN`             | _(unset)_           | If set, required for add/edit/delete actions                   |
| `WEB_HOST`              | `0.0.0.0`           | Web server bind address                                        |
| `WEB_PORT`              | `8000`              | Web server port                                                |
| `WEB_RELOAD`            | `false`             | Enable auto-reload (development)                               |
| `IR_LISTEN_TIMEOUT`     | `10`                | Default listen timeout                                         |

Set `MQTT_ENABLED=false` if you only need the REST/web components (for example when an MQTT broker is not available yet).  
Set `WEB_ENABLED=false` to run the bridge without starting the FastAPI server; MQTT and database services remain active.

## Running locally

### Windows (PowerShell)

```powershell
cd flirc-bridge

# Create .env file with your MQTT credentials
@"
MQTT_BROKER=192.168.2.4
MQTT_PORT=1883
MQTT_USERNAME=homeassistant
MQTT_PASSWORD=super_secret
INSTANCE_NAME=Living Room Bridge
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
cd flirc-bridge

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
python run_flirc_bridge.py
```

Visit `http://localhost:8000` for the management interface.

## Docker

### Build manually

```bash
docker build -f docker/Dockerfile -t bluscream/flirc-bridge .
docker run --rm \
  -e MQTT_BROKER=192.168.1.10 \
  -e MQTT_USERNAME=homeassistant \
  -e MQTT_PASSWORD=secret \
  -e MQTT_ENABLED=true \
  -v $(pwd)/patterns.db:/data/patterns.db \
  --device /dev/bus/usb:/dev/bus/usb \
  -p 8000:8000 \
  bluscream/flirc-bridge
```

### Using docker compose

```bash
# copy sample env if needed
cp docker/.env.example docker/.env

# adjust values inside docker/.env (MQTT, paths, etc.)

# start using docker hub image
docker compose -f docker/docker-compose.yml up -d
```

The compose file pulls `bluscream1/flirc-bridge:latest` and mounts `../patterns.db` relative to the compose directory. Adjust paths if you store the database elsewhere.

Mount additional devices (e.g., `--device /dev/input/...`) if required by `irtools`.

### Unraid

Import `unraid/template.xml` into your Unraid templates directory to expose the container in the UI.

## REST examples

### Create a device

```bash
curl -X POST http://localhost:8000/api/devices \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Compact Space Heater",
    "description": "Living room heater"
  }'
```

The response includes the `id` (UUID) for the device.

### Create an action

```bash
curl -X POST http://localhost:8000/api/actions \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "DEVICE_UUID_FROM_PREVIOUS_STEP",
    "name": "Toggle",
    "description": "Power toggle"
  }'
```

### Add a pattern to that action

```bash
curl -X POST http://localhost:8000/api/patterns \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "DEVICE_UUID_FROM_PREVIOUS_STEP",
    "action_id": "ACTION_UUID_FROM_PREVIOUS_STEP",
    "patterns": [
      {
        "format": "raw",
        "data": ["+9094 -4399 ..."],
        "repeat": 1,
        "ik": 23000
      }
    ]
  }'
```

### Send a stored action

```bash
curl -X POST http://localhost:8000/api/send \
  -H "Content-Type: application/json" \
  -d '{"device": "DEVICE_UUID", "action": "ACTION_UUID"}'
```

### Send a specific pattern

```bash
curl -X POST http://localhost:8000/api/send \
  -H "Content-Type: application/json" \
  -d '{"pattern": "PATTERN_UUID"}'
```

### Send a custom pattern (and optionally save it)

```bash
curl -X POST http://localhost:8000/api/send \
  -H "Content-Type: application/json" \
  -d '{
    "format": "raw",
    "data": ["+9094 -4399 ..."],
    "repeat": 1,
    "ik": 23000,
    "save": true,
    "device_name": "Compact Space Heater",
    "action_name": "Toggle"
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

### Export the full pattern catalogue

```bash
curl http://localhost:8000/api/patterns.json
```

Sample response (truncated):

```json
{
  "devices": [
    {
      "id": "0f7a3a5b-1e3a-475a-9f7c-4f5f6fe70f54",
      "name": "Compact Space Heater",
      "actions": [
        {
          "id": "1a42291d-7f57-4a41-bf1c-0f805f0f8a8f",
          "name": "Toggle",
          "patterns": [
            {
              "id": "8df1adac-1aa5-4b3d-9f44-1f466c0b1ead",
              "format": "raw",
              "repeat": 1,
              "ik": 23000,
              "data": ["+9094", "-4399", "..."]
            }
          ]
        }
      ]
    }
  ]
}
```

## MQTT custom payload

Publish to `flirc_bridge/send`:

```json
{
  "format": "raw",
  "data": ["+9094 -4399 ..."],
  "ik": 23000,
  "repeat": 1
}
```