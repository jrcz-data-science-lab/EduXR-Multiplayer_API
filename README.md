# OpenXrMp Session Registry (Python)

Small on-prem registry API for dedicated server discovery.

## What it does

- `POST /sessions` - register/create a session row (optionally launch dedicated server process).
- `GET /sessions` - return discoverable sessions for Unreal clients.
- `POST /sessions/{sessionId}/heartbeat` - keep session alive.
- `DELETE /sessions/{sessionId}` - remove session row.
- `GET /health` - health endpoint.
- `GET /admin` - simple HTML admin panel.
- `GET /admin/sessions` - rich admin session view (`createdAt`, `lastHeartbeatAt`, `staleAgeSeconds`, `isStale`).

This matches the dedicated flow in `XrMpGameInstance` where:

- Host calls `POST /sessions`
- Client browser calls `GET /sessions`
- Join uses returned `connectString` (or derived `address:port`)

## Run

```powershell
Push-Location "C:\Users\ZeroR\Documents\Unreal Projects\OpenXrMp\SessionRegistryApi"
python .\registry_server.py --host 0.0.0.0 --port 8080 --token change-me
Pop-Location
```

Environment variable equivalents:

- `SESSION_REGISTRY_HOST`
- `SESSION_REGISTRY_PORT`
- `SESSION_REGISTRY_TOKEN`
- `SESSION_REGISTRY_TTL_SECONDS`
- `SESSION_REGISTRY_CLEANUP_INTERVAL`

## Unreal setup

In your `BP_Gameinstance` (derived from `XrMpGameInstance`):

- `SetNetworkMode(Dedicated)`
- Set:
  - `DedicatedApiBaseUrl` -> `http://<server-ip>:8080`
  - `DedicatedApiListRoute` -> `/sessions`
  - `DedicatedApiCreateRoute` -> `/sessions`
  - `DedicatedApiToken` -> same token as server

## API examples

Create/register:

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

Create + launch server script in one request:

```json
{
  "serverName": "Teacher Session",
  "connectAddress": "10.0.0.25",
  "connectPort": 7777,
  "maxPlayers": 16,
  "map": "/Game/VRTemplate/VRTemplateMap",
  "launch": {
    "scriptPath": "/home/ubuntu/OpenXrMp/start_server.sh",
    "scriptInterpreter": "/bin/bash",
    "cwd": "/home/ubuntu/OpenXrMp",
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

Supported placeholders in `launch.scriptArgs`, `launch.scriptPath`, `launch.cwd`, and `launch.env` values:

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

## One-click launch setup (recommended)

This repo includes:

- `start_server.sh` - wrapper script the API can launch
- `admin_launch_payload.json` - copy-paste payload template for `POST /sessions`

On Ubuntu, copy `start_server.sh` to your API folder and make it executable:

```bash
chmod +x /home/ubuntu/SessionRegistryApi/start_server.sh
```

For your build location from Windows (`C:\Users\ZeroR\Documents\GAMEBUILD\LinuxServer`), after transfer/extract on Ubuntu the equivalent root is typically:

- `/home/ubuntu/OpenXrMpServer/LinuxServer`

Set this through launch env (`OPENXR_SERVER_ROOT`) as shown in `admin_launch_payload.json`.

If you create sessions from `/admin`, use these values:

- `Launch Script Path` -> `/home/ubuntu/SessionRegistryApi/start_server.sh`
- `Script Interpreter` -> `/bin/bash`
- `Launch Working Dir` -> `/home/ubuntu/SessionRegistryApi`
- `Script Args (JSON)` -> `["{connectPort}","{map}","{maxPlayers}","{serverName}","{sessionId}"]`

Then create a session row; registry will launch the script and return `launchPid`/`launchStatus` in API responses.

Admin list response (richer metadata):

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

## Heartbeat Client (Create -> Heartbeat -> Delete)

Use `heartbeat_client.py` on the dedicated server host so session rows stay fresh and are cleaned up on shutdown.

```powershell
Push-Location "C:\Users\ZeroR\Documents\Unreal Projects\OpenXrMp\SessionRegistryApi"
python .\heartbeat_client.py \
  --base-url http://127.0.0.1:8080 \
  --token change-me \
  --connect-address 10.0.0.25 \
  --connect-port 7777 \
  --server-name "Teacher Session" \
  --heartbeat-interval 10
Pop-Location
```

Open the admin panel in browser:

```text
http://<server-ip>:8080/admin
```

Response includes:

```json
{
  "sessionId": "...",
  "serverName": "Teacher Session",
  "connectString": "10.0.0.25:7777",
  "maxPlayers": 16,
  "currentPlayers": 0
}
```

List response:

```json
{
  "sessions": [
    {
      "sessionId": "...",
      "serverName": "Teacher Session",
      "ownerName": "On-Prem Server",
      "connectString": "10.0.0.25:7777",
      "maxPlayers": 16,
      "currentPlayers": 0,
      "pingMs": -1,
      "buildUniqueId": 1,
      "mode": "dedicated",
      "map": "/Game/VRTemplate/VRTemplateMap"
    }
  ]
}
```

## Test

```powershell
Push-Location "C:\Users\ZeroR\Documents\Unreal Projects\OpenXrMp\SessionRegistryApi"
python -m unittest -v test_registry_server.py
Pop-Location
```

## Shipping the Linux server build (2GB+)

Source build output on your Windows machine (as you shared):

- `C:\Users\ZeroR\Documents\GAMEBUILD\LinuxServer`

Recommended transfer flow (better than GitHub Release for iterative internal deploys):

1. Archive locally on Windows.

```powershell
tar -czf "C:\Users\ZeroR\Documents\GAMEBUILD\OpenXrMpLinuxServer.tar.gz" -C "C:\Users\ZeroR\Documents\GAMEBUILD" "LinuxServer"
```

2. Copy to Ubuntu with `scp` (or WinSCP if you prefer GUI).

```powershell
scp "C:\Users\ZeroR\Documents\GAMEBUILD\OpenXrMpLinuxServer.tar.gz" ubuntu@<linux-server-ip>:/home/ubuntu/
```

3. Extract on Ubuntu.

```bash
mkdir -p /home/ubuntu/OpenXrMpServer
tar -xzf /home/ubuntu/OpenXrMpLinuxServer.tar.gz -C /home/ubuntu/OpenXrMpServer
```

Use GitHub Release only when you want versioned external distribution; for local on-prem testing, direct transfer is simpler and faster.

## Linux server notes

On Ubuntu, open the required ports:

- TCP `8080` for this registry API
- UDP `7777` for Unreal dedicated game traffic

You can run this registry as a `systemd` service and keep Unreal dedicated server as a separate service/process.

