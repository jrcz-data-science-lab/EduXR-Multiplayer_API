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
- `DELETE /sessions/{sessionId}` - remove a session row.
- `GET /health` - health check.
- `GET /admin` - browser admin panel.
- `GET /admin/sessions` - richer admin session view with timestamps and stale-age data.
### Lifecycle behavior
- `heartbeat` updates `lastHeartbeatAt`.
- `players` updates player counts and also refreshes the heartbeat.
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
- `Script Args (JSON)` - arguments passed to the launcher script
If `Server File Path (Linux)` is provided, the admin form sends it as `OPENXR_SERVER_SCRIPT` in the launch environment.
If `MultiHome IP` is provided, the launcher can forward it as `MULTIHOME_IP` so the Linux server binds to a specific interface.
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
4. The dedicated server reports player count changes with `POST /sessions/{sessionId}/players`.
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
## Heartbeat client
`heartbeat_client.py` is useful if you want a lightweight helper process to keep a session fresh and clean it up on shutdown.
```powershell
python .\heartbeat_client.py `
  --base-url http://127.0.0.1:8080 `
  --token change-me `
  --connect-address 10.0.0.25 `
  --connect-port 7777 `
  --server-name "Teacher Session" `
  --heartbeat-interval 10
```
## API responses
### `GET /admin/sessions`
```json
{
  "sessions": [
    {
      "sessionId": "...",
      "serverName": "Teacher Session",
      "connectString": "10.0.0.25:7777",
      "createdAt": "2026-04-07T16:45:00+00:00",
      "lastHeartbeatAt": "2026-04-07T16:45:09+00:00",
      "staleAgeSeconds": 9.13,
      "isStale": false
    }
  ],
  "ttlSeconds": 120,
  "generatedAt": "2026-04-07T16:45:09+00:00"
}
```
### `POST /sessions/{sessionId}/heartbeat`
```json
{
  "sessionId": "...",
  "status": "heartbeat_updated",
  "currentPlayers": 5
}
```
### `POST /sessions/{sessionId}/players`
```json
{
  "sessionId": "...",
  "status": "players_updated",
  "currentPlayers": 5,
  "maxPlayers": 16
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
