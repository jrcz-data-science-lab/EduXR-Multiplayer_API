#!/usr/bin/env bash
set -euo pipefail

# Launcher wrapper used by the registry when it starts a dedicated server.
# Usage: start_server.sh <port> <map> <maxPlayers> <serverName> <sessionId>

# Defaults match the common Unreal dedicated-server setup.
PORT="${1:-7777}"
MAP_NAME="${2:-/Game/VRTemplate/VRTemplateMap}"
MAX_PLAYERS="${3:-16}"
SERVER_NAME="${4:-OpenXrMp Dedicated}"
SESSION_ID="${5:-unknown-session}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The launcher can run from the repo root or a custom server root.
SERVER_ROOT="${OPENXR_SERVER_ROOT:-$SCRIPT_DIR}"
LOG_DIR="${OPENXR_LOG_DIR:-$SERVER_ROOT/logs}"

mkdir -p "$LOG_DIR"

# Prefer an explicit server script path when the admin UI or env sets it.
if [[ -n "${OPENXR_SERVER_SCRIPT:-}" ]]; then
  SERVER_SCRIPT="$OPENXR_SERVER_SCRIPT"
elif [[ -x "$SERVER_ROOT/OpenXrMpServer.sh" ]]; then
  SERVER_SCRIPT="$SERVER_ROOT/OpenXrMpServer.sh"
elif [[ -x "$SERVER_ROOT/LinuxServer/OpenXrMpServer.sh" ]]; then
  SERVER_SCRIPT="$SERVER_ROOT/LinuxServer/OpenXrMpServer.sh"
else
  echo "[start_server] Could not find OpenXrMpServer.sh under SERVER_ROOT=$SERVER_ROOT"
  echo "[start_server] Set OPENXR_SERVER_SCRIPT to the full path of OpenXrMpServer.sh"
  exit 1
fi

# Extra args let operators pass optional Unreal flags without editing the script.
EXTRA_ARGS=()
if [[ -n "${OPENXR_EXTRA_ARGS:-}" ]]; then
  # shellcheck disable=SC2206
  EXTRA_ARGS=(${OPENXR_EXTRA_ARGS})
fi

# Bind to a specific NIC when the registry or admin UI provides a server IP.
if [[ -n "${MULTIHOME_IP:-}" ]]; then
  EXTRA_ARGS+=("-MULTIHOME=${MULTIHOME_IP}")
fi

LOG_FILE="$LOG_DIR/server_${PORT}.log"

echo "[start_server] Launching sessionId=$SESSION_ID serverName=$SERVER_NAME port=$PORT map=$MAP_NAME maxPlayers=$MAX_PLAYERS"
echo "[start_server] Script: $SERVER_SCRIPT"
echo "[start_server] Log: $LOG_FILE"

# Use exec so the registry tracks the real Unreal server process PID.
exec "$SERVER_SCRIPT" "$MAP_NAME" "-port=$PORT" "-log" "-unattended" "-NoCrashDialog" "${EXTRA_ARGS[@]}" >>"$LOG_FILE" 2>&1

