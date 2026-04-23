import argparse
import ipaddress
import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def epoch_to_utc_iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


@dataclass
class SessionRecord:
    session_id: str
    server_name: str
    owner_name: str
    connect_string: str
    map_name: str
    max_players: int
    current_players: int
    ping_ms: int
    build_unique_id: int
    mode: str
    created_at: float = field(default_factory=time.time)
    last_heartbeat_at: float = field(default_factory=time.time)
    launch_pid: Optional[int] = None
    launch_command: str = ""
    launch_status: str = "not_launched"
    launch_exit_code: Optional[int] = None
    lifecycle_policy: str = "manual"
    idle_timeout_seconds: int = 900
    empty_since_at: Optional[float] = None


class SessionStore:
    def __init__(self, ttl_seconds: int, default_idle_shutdown_seconds: int = 900) -> None:
        self._ttl_seconds = max(1, ttl_seconds)
        self._default_idle_shutdown_seconds = max(1, default_idle_shutdown_seconds)
        self._lock = threading.Lock()
        self._sessions: Dict[str, SessionRecord] = {}
        self._processes: Dict[str, subprocess.Popen] = {}

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def create(self, payload: Dict[str, object]) -> SessionRecord:
        server_name = str(payload.get("serverName") or "Dedicated Server")
        owner_name = str(payload.get("ownerName") or "On-Prem Server")
        map_name = str(payload.get("map") or "/Game/VRTemplate/VRTemplateMap")
        mode = str(payload.get("mode") or "dedicated")

        max_players = int(payload.get("maxPlayers") or 16)
        current_players = int(payload.get("currentPlayers") or 0)
        ping_ms = int(payload.get("pingMs") or -1)
        build_unique_id = int(payload.get("buildUniqueId") or 1)
        lifecycle_policy = self._normalize_lifecycle_policy(payload.get("lifecyclePolicy"))
        idle_timeout_seconds = int(payload.get("idleTimeoutSeconds") or self._default_idle_shutdown_seconds)
        if idle_timeout_seconds < 1:
            raise ValueError("idleTimeoutSeconds must be >= 1")

        connect_string = str(payload.get("connectString") or "")
        if not connect_string:
            address = str(payload.get("connectAddress") or payload.get("address") or payload.get("host") or "")
            port = int(payload.get("connectPort") or payload.get("port") or 7777)
            if address:
                connect_string = f"{address}:{port}"

        if not connect_string:
            raise ValueError("connectString or address/port is required")

        session_id = str(payload.get("sessionId") or uuid.uuid4())
        now = time.time()

        record = SessionRecord(
            session_id=session_id,
            server_name=server_name,
            owner_name=owner_name,
            connect_string=connect_string,
            map_name=map_name,
            max_players=max_players,
            current_players=current_players,
            ping_ms=ping_ms,
            build_unique_id=build_unique_id,
            mode=mode,
            created_at=now,
            last_heartbeat_at=now,
            lifecycle_policy=lifecycle_policy,
            idle_timeout_seconds=idle_timeout_seconds,
            empty_since_at=now if current_players <= 0 else None,
        )

        launch_spec = self._build_launch_spec(payload)
        launched_process: Optional[subprocess.Popen] = None
        if launch_spec:
            launch_context = {
                "sessionId": session_id,
                "serverName": server_name,
                "ownerName": owner_name,
                "map": map_name,
                "maxPlayers": max_players,
                "currentPlayers": current_players,
                "connectString": connect_string,
                "connectAddress": connect_string.rsplit(":", 1)[0] if ":" in connect_string else connect_string,
                "connectPort": connect_string.rsplit(":", 1)[1] if ":" in connect_string else "7777",
                "buildUniqueId": build_unique_id,
                "mode": mode,
            }
            process, command = self._launch_process(launch_spec, launch_context)
            launched_process = process
            record.launch_pid = process.pid
            record.launch_command = command
            record.launch_status = "running" if process.poll() is None else "exited"
            record.launch_exit_code = process.poll()

        with self._lock:
            self._sessions[session_id] = record
            if launch_spec and record.launch_status == "running" and launched_process:
                self._processes[session_id] = launched_process

        return record

    def list_sessions(self) -> Dict[str, object]:
        now = time.time()
        with self._lock:
            for record in self._sessions.values():
                self._refresh_process_state_locked(record, now)
            records = [
                record for record in self._sessions.values()
                if (now - record.last_heartbeat_at) <= self._ttl_seconds or record.launch_status == "running"
            ]

        return {"sessions": [self._record_to_wire(record) for record in records]}

    def list_admin_sessions(self) -> Dict[str, object]:
        now = time.time()
        with self._lock:
            records = list(self._sessions.values())
            for record in records:
                self._refresh_process_state_locked(record, now)

        return {
            "sessions": [self._record_to_admin_wire(record, now) for record in records],
            "ttlSeconds": self._ttl_seconds,
            "generatedAt": utc_now_iso(),
        }

    def heartbeat(self, session_id: str, payload: Optional[Dict[str, object]] = None) -> Optional[SessionRecord]:
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            now = time.time()
            record.last_heartbeat_at = now
            self._apply_runtime_update_locked(record, payload or {}, now)
            return record

    def update_players(self, session_id: str, payload: Dict[str, object]) -> Optional[SessionRecord]:
        if "currentPlayers" not in payload:
            raise ValueError("currentPlayers is required")

        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            now = time.time()
            record.last_heartbeat_at = now
            self._apply_runtime_update_locked(record, payload, now)
            return record

    def delete(self, session_id: str) -> bool:
        process: Optional[subprocess.Popen] = None
        with self._lock:
            existed = self._sessions.pop(session_id, None) is not None
            process = self._processes.pop(session_id, None)

        if process:
            self._terminate_process(process)

        return existed

    def cleanup_expired(self) -> int:
        now = time.time()
        removed = 0
        processes_to_terminate: List[subprocess.Popen] = []
        with self._lock:
            for record in self._sessions.values():
                self._refresh_process_state_locked(record, now)
            expired_ids: List[str] = []
            for session_id, record in self._sessions.items():
                is_stale = (now - record.last_heartbeat_at) > self._ttl_seconds and record.launch_status != "running"
                is_idle_empty = (
                    record.launch_status == "running"
                    and record.lifecycle_policy == "auto_close_when_empty"
                    and record.current_players <= 0
                    and record.empty_since_at is not None
                    and (now - record.empty_since_at) >= record.idle_timeout_seconds
                )

                if is_stale or is_idle_empty:
                    expired_ids.append(session_id)
                    if is_idle_empty:
                        process = self._processes.get(session_id)
                        if process:
                            processes_to_terminate.append(process)

            for session_id in expired_ids:
                del self._sessions[session_id]
                self._processes.pop(session_id, None)
                removed += 1

        for process in processes_to_terminate:
            self._terminate_process(process)
        return removed

    @staticmethod
    def _normalize_lifecycle_policy(value: object) -> str:
        raw = str(value or "manual").strip().lower().replace("-", "_")
        if raw in {"manual", "always_on", "24_7"}:
            return "manual"
        if raw in {"auto_close_when_empty", "auto_close", "auto"}:
            return "auto_close_when_empty"
        raise ValueError("lifecyclePolicy must be 'manual' or 'auto_close_when_empty'")

    @staticmethod
    def _coerce_int(value: object, field_name: str) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError as ex:
                raise ValueError(f"{field_name} must be an integer") from ex
        raise ValueError(f"{field_name} must be an integer")

    def _apply_runtime_update_locked(self, record: SessionRecord, payload: Dict[str, object], now: float) -> None:
        if "currentPlayers" in payload:
            record.current_players = max(0, self._coerce_int(payload.get("currentPlayers"), "currentPlayers"))
        if "maxPlayers" in payload:
            record.max_players = max(1, self._coerce_int(payload.get("maxPlayers"), "maxPlayers"))
        if "pingMs" in payload:
            record.ping_ms = self._coerce_int(payload.get("pingMs"), "pingMs")

        if record.current_players > 0:
            record.empty_since_at = None
        elif record.empty_since_at is None:
            record.empty_since_at = now

    @staticmethod
    def _coerce_string_list(value: object) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except json.JSONDecodeError:
                pass
            return [stripped]
        return []

    def _build_launch_spec(self, payload: Dict[str, object]) -> Optional[Dict[str, object]]:
        launch_payload = payload.get("launch") if isinstance(payload.get("launch"), dict) else {}

        script_path = str(
            launch_payload.get("scriptPath")
            or payload.get("launchScriptPath")
            or ""
        ).strip()
        if not script_path:
            return None

        script_args = self._coerce_string_list(
            launch_payload.get("scriptArgs")
            or payload.get("launchScriptArgs")
        )
        interpreter = str(
            launch_payload.get("scriptInterpreter")
            or payload.get("launchScriptInterpreter")
            or ""
        ).strip()
        cwd = str(launch_payload.get("cwd") or payload.get("launchCwd") or "").strip() or None

        env_value = launch_payload.get("env") or payload.get("launchEnv") or {}
        launch_env: Dict[str, str] = {}
        if isinstance(env_value, dict):
            launch_env = {str(k): str(v) for k, v in env_value.items()}

        multihome_ip_raw = (
            launch_payload.get("multiHomeIp")
            or launch_payload.get("multihomeIp")
            or payload.get("multiHomeIp")
            or payload.get("multihomeIp")
            or launch_env.get("MULTIHOME_IP")
            or ""
        )
        multihome_ip = str(multihome_ip_raw).strip()
        if multihome_ip:
            try:
                ipaddress.ip_address(multihome_ip)
            except ValueError as ex:
                raise ValueError("multiHomeIp must be a valid IPv4 or IPv6 address") from ex
            launch_env["MULTIHOME_IP"] = multihome_ip

        return {
            "scriptPath": script_path,
            "scriptArgs": script_args,
            "scriptInterpreter": interpreter,
            "cwd": cwd,
            "env": launch_env,
        }

    @staticmethod
    def _expand_template(value: str, context: Dict[str, object]) -> str:
        class _SafeDict(dict):
            def __missing__(self, key):
                return "{" + str(key) + "}"

        return value.format_map(_SafeDict(context))

    def _launch_process(self, launch_spec: Dict[str, object], context: Dict[str, object]) -> Tuple[subprocess.Popen, str]:
        script_path = self._expand_template(str(launch_spec.get("scriptPath") or ""), context)
        if not script_path:
            raise ValueError("launch scriptPath is required")

        script_args = [
            self._expand_template(str(arg), context)
            for arg in self._coerce_string_list(launch_spec.get("scriptArgs"))
        ]
        interpreter = self._expand_template(str(launch_spec.get("scriptInterpreter") or ""), context).strip()
        cwd_raw = str(launch_spec.get("cwd") or "").strip()
        cwd = self._expand_template(cwd_raw, context) if cwd_raw else None

        env = os.environ.copy()
        launch_env = launch_spec.get("env")
        if isinstance(launch_env, dict):
            for key, value in launch_env.items():
                env[str(key)] = self._expand_template(str(value), context)

        command = [interpreter, script_path, *script_args] if interpreter else [script_path, *script_args]
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env)
        except OSError as ex:
            raise ValueError(f"failed to launch script: {ex}") from ex

        return process, " ".join(command)

    def _refresh_process_state_locked(self, record: SessionRecord, now: float) -> None:
        if not record.launch_pid:
            return

        process = self._processes.get(record.session_id)
        if not process:
            return

        exit_code = process.poll()
        if exit_code is None:
            record.launch_status = "running"
            record.launch_exit_code = None
        else:
            record.launch_status = "exited"
            record.launch_exit_code = int(exit_code)
            self._processes.pop(record.session_id, None)

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return

        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=2)
            except Exception:
                pass

    @staticmethod
    def _record_to_wire(record: SessionRecord) -> Dict[str, object]:
        return {
            "sessionId": record.session_id,
            "serverName": record.server_name,
            "ownerName": record.owner_name,
            "connectString": record.connect_string,
            "maxPlayers": record.max_players,
            "currentPlayers": record.current_players,
            "pingMs": record.ping_ms,
            "buildUniqueId": record.build_unique_id,
            "mode": record.mode,
            "map": record.map_name,
            "launchPid": record.launch_pid,
            "launchStatus": record.launch_status,
            "launchExitCode": record.launch_exit_code,
            "launchCommand": record.launch_command,
            "lifecyclePolicy": record.lifecycle_policy,
            "idleTimeoutSeconds": record.idle_timeout_seconds,
            "emptySinceAt": epoch_to_utc_iso(record.empty_since_at) if record.empty_since_at else None,
        }

    def _record_to_admin_wire(self, record: SessionRecord, now: float) -> Dict[str, object]:
        stale_age = max(0.0, now - record.last_heartbeat_at)
        base = self._record_to_wire(record)
        base.update(
            {
                "createdAt": epoch_to_utc_iso(record.created_at),
                "lastHeartbeatAt": epoch_to_utc_iso(record.last_heartbeat_at),
                "staleAgeSeconds": round(stale_age, 3),
                "isStale": stale_age > self._ttl_seconds,
                "emptyAgeSeconds": round(max(0.0, now - record.empty_since_at), 3) if record.empty_since_at else 0.0,
            }
        )
        return base


def start_cleanup_loop(store: SessionStore, interval_seconds: int) -> Tuple[threading.Event, threading.Thread]:
    stop_event = threading.Event()

    def _worker() -> None:
        while not stop_event.wait(max(1, interval_seconds)):
            store.cleanup_expired()

    thread = threading.Thread(target=_worker, name="session-cleanup", daemon=True)
    thread.start()
    return stop_event, thread


def make_handler(store: SessionStore, bearer_token: str):
    admin_index_path = Path(__file__).resolve().parent / "admin" / "index.html"

    class RegistryHandler(BaseHTTPRequestHandler):
        server_version = "OpenXrMpSessionRegistry/1.0"

        def _json_response(self, status: int, payload: Dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _text_response(self, status: int, body: str, content_type: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _read_json(self) -> Dict[str, object]:
            raw_len = self.headers.get("Content-Length", "0")
            length = int(raw_len) if raw_len.isdigit() else 0
            raw = self.rfile.read(length) if length > 0 else b"{}"
            if not raw:
                return {}
            try:
                parsed = json.loads(raw.decode("utf-8"))
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}

        def _is_authorized(self) -> bool:
            if not bearer_token:
                return True
            auth_header = self.headers.get("Authorization", "")
            expected = f"Bearer {bearer_token}"
            return auth_header == expected

        def _require_auth(self) -> bool:
            if self._is_authorized():
                return True
            self._json_response(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return False

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/health":
                self._json_response(HTTPStatus.OK, {"status": "ok"})
                return

            if path in {"/admin", "/admin/", "/admin/index.html"}:
                if admin_index_path.exists():
                    self._text_response(HTTPStatus.OK, admin_index_path.read_text(encoding="utf-8"), "text/html; charset=utf-8")
                else:
                    self._text_response(HTTPStatus.NOT_FOUND, "admin/index.html not found", "text/plain; charset=utf-8")
                return

            if path == "/admin/sessions":
                if not self._require_auth():
                    return
                self._json_response(HTTPStatus.OK, store.list_admin_sessions())
                return

            if path == "/sessions":
                if not self._require_auth():
                    return
                self._json_response(HTTPStatus.OK, store.list_sessions())
                return

            self._json_response(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/sessions":
                if not self._require_auth():
                    return
                payload = self._read_json()
                try:
                    created = store.create(payload)
                except ValueError as ex:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(ex)})
                    return
                self._json_response(HTTPStatus.CREATED, store._record_to_wire(created))
                return

            if path.startswith("/sessions/") and path.endswith("/players"):
                if not self._require_auth():
                    return
                parts = [p for p in path.split("/") if p]
                if len(parts) != 3:
                    self._json_response(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                session_id = parts[1]
                players_payload = self._read_json()
                try:
                    touched = store.update_players(session_id, players_payload)
                except ValueError as ex:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(ex)})
                    return
                if not touched:
                    self._json_response(HTTPStatus.NOT_FOUND, {"error": "session_not_found"})
                    return
                self._json_response(
                    HTTPStatus.OK,
                    {
                        "sessionId": session_id,
                        "status": "players_updated",
                        "currentPlayers": touched.current_players,
                        "maxPlayers": touched.max_players,
                    },
                )
                return

            if path.startswith("/sessions/") and path.endswith("/heartbeat"):
                if not self._require_auth():
                    return
                parts = [p for p in path.split("/") if p]
                if len(parts) != 3:
                    self._json_response(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                session_id = parts[1]
                heartbeat_payload = self._read_json()
                touched = store.heartbeat(session_id, heartbeat_payload)
                if not touched:
                    self._json_response(HTTPStatus.NOT_FOUND, {"error": "session_not_found"})
                    return
                self._json_response(
                    HTTPStatus.OK,
                    {
                        "sessionId": session_id,
                        "status": "heartbeat_updated",
                        "currentPlayers": touched.current_players,
                    },
                )
                return

            self._json_response(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_DELETE(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path.startswith("/sessions/"):
                if not self._require_auth():
                    return
                parts = [p for p in path.split("/") if p]
                if len(parts) != 2:
                    self._json_response(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                session_id = parts[1]
                deleted = store.delete(session_id)
                if not deleted:
                    self._json_response(HTTPStatus.NOT_FOUND, {"error": "session_not_found"})
                    return
                self._json_response(HTTPStatus.OK, {"sessionId": session_id, "status": "deleted"})
                return

            self._json_response(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def log_message(self, fmt: str, *args):
            print(f"[registry] {self.client_address[0]} - {fmt % args}")

    return RegistryHandler


def build_server(
    host: str,
    port: int,
    token: str,
    ttl_seconds: int,
    cleanup_interval: int,
    idle_shutdown_seconds: int = 900,
) -> Tuple[ThreadingHTTPServer, threading.Event, threading.Thread]:
    store = SessionStore(ttl_seconds=ttl_seconds, default_idle_shutdown_seconds=idle_shutdown_seconds)
    handler_cls = make_handler(store, token)
    httpd = ThreadingHTTPServer((host, port), handler_cls)  # type: ignore[arg-type]
    stop_event, cleanup_thread = start_cleanup_loop(store, cleanup_interval)
    return httpd, stop_event, cleanup_thread


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenXrMp dedicated session registry")
    parser.add_argument("--host", default=os.getenv("SESSION_REGISTRY_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("SESSION_REGISTRY_PORT", "8080")))
    parser.add_argument("--token", default=os.getenv("SESSION_REGISTRY_TOKEN", ""))
    parser.add_argument("--ttl-seconds", type=int, default=int(os.getenv("SESSION_REGISTRY_TTL_SECONDS", "120")))
    parser.add_argument("--cleanup-interval", type=int, default=int(os.getenv("SESSION_REGISTRY_CLEANUP_INTERVAL", "10")))
    parser.add_argument("--idle-shutdown-seconds", type=int, default=int(os.getenv("SESSION_REGISTRY_IDLE_SHUTDOWN_SECONDS", "900")))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server, stop_event, cleanup_thread = build_server(
        host=args.host,
        port=args.port,
        token=args.token,
        ttl_seconds=args.ttl_seconds,
        cleanup_interval=args.cleanup_interval,
        idle_shutdown_seconds=args.idle_shutdown_seconds,
    )

    bind_host, bind_port = server.server_address
    print(f"Session registry listening on http://{bind_host}:{bind_port}")
    print(f"Auth token enabled: {'yes' if args.token else 'no'}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        cleanup_thread.join(timeout=1.0)
        server.server_close()


if __name__ == "__main__":
    main()

