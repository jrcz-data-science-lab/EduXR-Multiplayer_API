# OpenXrMp Session Registry (Python)
OpenXrMp Session Registry is a small on-prem API for listing, creating, and tracking dedicated Unreal sessions.
It includes:
- `registry_server.py` - Python registry API
- `heartbeat_client.py` - optional helper client for automated heartbeats
- `admin/index.html` - browser admin panel
- `start_server.sh` - Linux launch wrapper for dedicated servers
## What it does
- `POST /sessions` - create a session row, optionally launching a server process.
- `GET /sessions` - return discoverable sessions for Unreal clients.
- `POST /sessions/{sessionId}/heartbeat` - keep a session alive.
- `POST /sessions/{sessionId}/players` - update `currentPlayers` and optional `maxPlayers`.
- `POST /sessions/{sessionId}/player-events` - apply player-count deltas (`join`/`leave` or integer `delta`) on the API side.
- `DELETE /sessions/{sessionId}` - remove a session row.
- `GET /health` - health check.
- `GET /admin` - browser admin panel.
- `GET /admin/sessions` - richer admin session view with timestamps and stale-age data.
### Lifecycle behavior
- `heartbeat` updates `lastHeartbeatAt`.
- `players` updates player counts and also refreshes the heartbeat.
- `player-events` applies API-side count deltas and supports idempotency via `eventId`.
- When a session is in delta mode, heartbeat `currentPlayers` values are ignored unless `authoritativePlayers=true` is provided.
- Sessions launched through the registry stay visible while the launched process is still running.
- `DELETE` removes the row and terminates the launched process if one exists.
- Two lifecycle policies are supported when creating a session:
  - `manual` - keep the server running until you stop it manually.
  - `auto_close_when_empty` - stop and remove the server after it has had `0` players for `idleTimeoutSeconds`.
## Repository layout
```text
SessionRegistryApi/
├── admin/
│   └── index.html
├── heartbeat_client.py
├── registry_server.py
├── requirements.txt
├── start_server.sh
├── test_registry_server.py
└── README.md
```
## Requirements
- Python 3.10+ (3.11+ recommended)
- `pip`
- A Linux host if you plan to launch an Unreal dedicated server from the registry
Install dependencies:
```powershell
python -m pip install -r requirements.txt
```
## Run the registry
Start the API locally:
```powershell
python .\registry_server.py --host 0.0.0.0 --port 8080 --token change-me
```
Environment variables are also supported:
- `SESSION_REGISTRY_HOST`
- `SESSION_REGISTRY_PORT`
- `SESSION_REGISTRY_TOKEN`
- `SESSION_REGISTRY_TTL_SECONDS`
- `SESSION_REGISTRY_CLEANUP_INTERVAL`
- `SESSION_REGISTRY_IDLE_SHUTDOWN_SECONDS`
## Admin panel
Open the browser admin UI at:
```text
http://<registry-ip>:8080/admin
```
The admin form lets you configure:
- API base URL
- bearer token
- session details
- launch script path
- working directory
- Linux server executable path
- lifecycle policy
- idle timeout
The form sends its launch settings directly; there is no separate payload template file.

### Admin launch fields
If you use the admin panel to launch a Linux dedicated server, set:
- `Launch Script Path` - path to `start_server.sh`
- `Script Interpreter` - usually `/bin/bash`
- `Launch Working Dir` - folder containing `start_server.sh`
- `Server File Path (Linux)` - full path to the packaged Linux server script or binary on the Linux host
- `MultiHome IP` - optional bind address for the server NIC
- `Script Args (JSON)` - arguments passed to the launcher script as a JSON array (e.g., `["{connectPort}", "{map}", "{maxPlayers}", "{serverName}", "{sessionId}"]`)
If `Server File Path (Linux)` is provided, the admin form sends it as `OPENXR_SERVER_SCRIPT` in the launch environment.
If `MultiHome IP` is provided, the launcher can forward it as `MULTIHOME_IP` so the Linux server binds to a specific interface.
Placeholder values like `{sessionId}`, `{connectPort}`, `{map}`, etc. are automatically expanded before launching the process.
## Unreal setup
In your Unreal project, use the dedicated-server flow from `XrMpGameInstance`.
Set the registry values used by your game instance:
- `SessionRegistryBaseUrl` -> `http://<registry-ip>:8080`
- `SessionRegistryToken` -> your registry token
- `SessionId` -> provided at server launch (for heartbeat and player updates)
Typical flow:
1. Host creates a session with `POST /sessions`.
2. Clients query `GET /sessions`.
3. Clients join using the returned `connectString`.
4. The dedicated server reports player count changes with `POST /sessions/{sessionId}/player-events` (recommended) or `POST /sessions/{sessionId}/players`.
## Session creation examples
### Create a session without launching a process
```json
{
  "serverName": "Teacher Session",
  "ownerName": "On-Prem Server",
  "connectAddress": "10.0.0.25",
  "connectPort": 7777,
  "maxPlayers": 16,
  "currentPlayers": 0,
  "buildUniqueId": 1,
  "mode": "dedicated",
  "map": "/Game/VRTemplate/VRTemplateMap"
}
```
### Create and launch a dedicated server
```json
{
  "serverName": "Teacher Session",
  "connectAddress": "10.0.0.25",
  "connectPort": 7777,
  "maxPlayers": 16,
  "map": "/Game/VRTemplate/VRTemplateMap",
  "lifecyclePolicy": "auto_close_when_empty",
  "idleTimeoutSeconds": 900,
  "launch": {
    "scriptPath": "/path/to/start_server.sh",
    "scriptInterpreter": "/bin/bash",
    "cwd": "/path/to/repo-root",
    "multiHomeIp": "10.0.0.25",
    "scriptArgs": [
      "{connectPort}",
      "{map}",
      "{maxPlayers}",
      "{serverName}",
      "{sessionId}"
    ]
  }
}
```
### Supported placeholders
These placeholders can be used in `launch.scriptArgs`, `launch.scriptPath`, `launch.cwd`, and `launch.env` values:
- `{sessionId}`
- `{serverName}`
- `{ownerName}`
- `{map}`
- `{maxPlayers}`
- `{currentPlayers}`
- `{connectAddress}`
- `{connectPort}`
- `{connectString}`
- `{buildUniqueId}`
- `{mode}`
## Update player counts from Unreal
Use the runtime player update endpoint from the dedicated server when players join or leave:
```http
POST /sessions/{sessionId}/players
Content-Type: application/json
Authorization: Bearer <token>
```
Example body:
```json
{
  "currentPlayers": 5,
  "maxPlayers": 16
}
```
This keeps the registry in sync with the authoritative server-side player count and also refreshes the session heartbeat.

For API-side counting (recommended when join/leave timing is noisy), send player events:
```http
POST /sessions/{sessionId}/player-events
Content-Type: application/json
Authorization: Bearer <token>
```

Example join payload:
```json
{
  "event": "join",
  "eventId": "join-4f7b6a57",
  "maxPlayers": 16
}
```

Example leave payload:
```json
{
  "event": "leave",
  "eventId": "leave-4f7b6a57"
}
```

You can also send an explicit integer delta instead of `event`:
```json
{
  "delta": 1,
  "eventId": "custom-delta-001"
}
```

`eventId` is optional, but recommended for idempotency so retried events do not double-apply.
## Heartbeat client
`heartbeat_client.py` is useful if you want a lightweight helper process to create a session, keep it fresh, and clean it up on shutdown.
```powershell
python .\heartbeat_client.py `
  --base-url http://127.0.0.1:8080 `
  --token change-me `
  --server-name "Teacher Session" `
  --owner-name "On-Prem Server" `
  --connect-address 10.0.0.25 `
  --connect-port 7777 `
  --max-players 16 `
  --heartbeat-interval 10
```

Available options:
- `--base-url` - Registry base URL (required)
- `--token` - Bearer token for authentication (optional, defaults to empty)
- `--server-name` - Display name for the server (default: "Dedicated Server")
- `--owner-name` - Owner/operator name (default: "On-Prem Server")
- `--connect-address` - IP address clients should connect to (required)
- `--connect-port` - Port number (default: 7777)
- `--max-players` - Maximum players allowed (default: 16)
- `--current-players` - Initial player count (default: 0)
- `--build-unique-id` - Build identifier (default: 1)
- `--mode` - Session mode (default: "dedicated")
- `--map` - Game map path (default: "/Game/VRTemplate/VRTemplateMap")
- `--heartbeat-interval` - Heartbeat frequency in seconds (default: 10.0)
- `--request-timeout` - HTTP timeout in seconds (default: 5)
## API responses

### `POST /sessions`
Creates a new session and optionally launches a server. Response includes full session details:
```json
{
  "sessionId": "550e8400-e29b-41d4-a716-446655440000",
  "serverName": "Teacher Session",
  "ownerName": "On-Prem Server",
  "connectString": "10.0.0.25:7777",
  "maxPlayers": 16,
  "currentPlayers": 0,
  "pingMs": -1,
  "buildUniqueId": 1,
  "mode": "dedicated",
  "map": "/Game/VRTemplate/VRTemplateMap",
  "launchPid": 12345,
  "launchStatus": "running",
  "launchExitCode": null,
  "launchCommand": "/bin/bash /path/to/start_server.sh 7777 /Game/VRTemplate/VRTemplateMap 16 Teacher Session ...",
  "lifecyclePolicy": "auto_close_when_empty",
  "idleTimeoutSeconds": 900,
  "emptySinceAt": null
}
```

### `GET /sessions`
Returns discoverable sessions (requires bearer token):
```json
{
  "sessions": [
    {
      "sessionId": "550e8400-e29b-41d4-a716-446655440000",
      "serverName": "Teacher Session",
      "connectString": "10.0.0.25:7777",
      "maxPlayers": 16,
      "currentPlayers": 1,
      "pingMs": 25,
      ...other fields...
    }
  ]
}
```

### `GET /admin/sessions`
Returns detailed admin view with timestamps and stale-age data (requires bearer token):
```json
{
  "sessions": [
    {
      "sessionId": "550e8400-e29b-41d4-a716-446655440000",
      "serverName": "Teacher Session",
      "connectString": "10.0.0.25:7777",
      "createdAt": "2026-04-07T16:45:00+00:00",
      "lastHeartbeatAt": "2026-04-07T16:45:09+00:00",
      "staleAgeSeconds": 9.13,
      "isStale": false,
      "emptyAgeSeconds": 0.0,
      ...other fields...
    }
  ],
  "ttlSeconds": 120,
  "generatedAt": "2026-04-07T16:45:09+00:00"
}
```
### `POST /sessions/{sessionId}/heartbeat`
Keeps a session alive. Automatically updates timestamps and can include runtime stats.
```json
{
  "sessionId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "heartbeat_updated",
  "currentPlayers": 5
}
```

### `POST /sessions/{sessionId}/players`
Updates player counts and refreshes the heartbeat automatically.
```json
{
  "sessionId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "players_updated",
  "currentPlayers": 5,
  "maxPlayers": 16
}
```

### `POST /sessions/{sessionId}/player-events`
Applies API-side player-count deltas with optional idempotency.
```json
{
  "sessionId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "player_event_applied",
  "currentPlayers": 5,
  "maxPlayers": 16,
  "playerCountSource": "delta"
}
```

### `DELETE /sessions/{sessionId}`
Removes a session and terminates any launched process.
```json
{
  "sessionId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "deleted"
}
```

### `GET /health`
Simple health check endpoint (no authentication required).
```json
{
  "status": "ok"
}
```
## Test
Run the test suite:
```powershell
python -m unittest -v test_registry_server.py
```
## Linux deployment notes
If you run the registry and Unreal dedicated server on Ubuntu, you will usually need:
- TCP `8080` for the registry API
- UDP `7777` for the Unreal server
The dedicated server build can live anywhere on the Linux machine. Point the admin form at the correct launcher script and server path for your environment.
You can also run the registry as a `systemd` service if you want it to start automatically.
