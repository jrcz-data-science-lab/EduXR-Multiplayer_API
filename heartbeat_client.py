import argparse
import json
import signal
import threading
import time
from typing import Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request_json(base_url: str, path: str, method: str, token: str, payload: Optional[Dict[str, object]] = None, timeout: int = 5) -> Dict[str, object]:
    body = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url=f"{base_url}{path}", data=body, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as response:
        text = response.read().decode("utf-8")
        return json.loads(text) if text else {}


def normalize_base_url(raw: str) -> str:
    normalized = raw.strip().rstrip("/")
    if not normalized:
        raise ValueError("base URL cannot be empty")
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Heartbeat sidecar for OpenXrMp session registry")
    parser.add_argument("--base-url", required=True, help="Registry base URL, e.g. http://10.0.0.25:8080")
    parser.add_argument("--token", default="", help="Bearer token used by registry")
    parser.add_argument("--heartbeat-interval", type=float, default=10.0, help="Heartbeat interval in seconds")
    parser.add_argument("--request-timeout", type=int, default=5, help="HTTP timeout in seconds")

    parser.add_argument("--server-name", default="Dedicated Server")
    parser.add_argument("--owner-name", default="On-Prem Server")
    parser.add_argument("--connect-address", required=True, help="IP/host clients should connect to")
    parser.add_argument("--connect-port", type=int, default=7777)
    parser.add_argument("--max-players", type=int, default=16)
    parser.add_argument("--current-players", type=int, default=0)
    parser.add_argument("--build-unique-id", type=int, default=1)
    parser.add_argument("--mode", default="dedicated")
    parser.add_argument("--map", dest="map_name", default="/Game/VRTemplate/VRTemplateMap")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_url = normalize_base_url(args.base_url)
    stop_event = threading.Event()

    def _handle_stop(signum, frame):
        del signum, frame
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_stop)

    create_payload = {
        "serverName": args.server_name,
        "ownerName": args.owner_name,
        "connectAddress": args.connect_address,
        "connectPort": args.connect_port,
        "maxPlayers": args.max_players,
        "currentPlayers": args.current_players,
        "buildUniqueId": args.build_unique_id,
        "mode": args.mode,
        "map": args.map_name,
    }

    session_id: Optional[str] = None
    try:
        created = request_json(
            base_url=base_url,
            path="/sessions",
            method="POST",
            token=args.token,
            payload=create_payload,
            timeout=args.request_timeout,
        )
        session_id = str(created.get("sessionId") or "")
        connect_string = str(created.get("connectString") or f"{args.connect_address}:{args.connect_port}")

        if not session_id:
            raise RuntimeError("Registry did not return sessionId")

        print(f"Registered session: id={session_id}, connect={connect_string}")

        while not stop_event.wait(max(0.5, args.heartbeat_interval)):
            try:
                request_json(
                    base_url=base_url,
                    path=f"/sessions/{session_id}/heartbeat",
                    method="POST",
                    token=args.token,
                    payload={},
                    timeout=args.request_timeout,
                )
                print(f"Heartbeat OK for session {session_id}")
            except (HTTPError, URLError, OSError, json.JSONDecodeError) as ex:
                print(f"Heartbeat error: {ex}")

    except (HTTPError, URLError, OSError, json.JSONDecodeError, RuntimeError, ValueError) as ex:
        print(f"Failed to start heartbeat client: {ex}")
        raise SystemExit(1)
    finally:
        if session_id:
            try:
                request_json(
                    base_url=base_url,
                    path=f"/sessions/{session_id}",
                    method="DELETE",
                    token=args.token,
                    payload=None,
                    timeout=args.request_timeout,
                )
                print(f"Deleted session {session_id}")
            except (HTTPError, URLError, OSError, json.JSONDecodeError) as ex:
                print(f"Best-effort delete failed for {session_id}: {ex}")


if __name__ == "__main__":
    main()

