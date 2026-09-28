"""Keep the Render SQLite workspace mirrored into this laptop's PostgreSQL."""

from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from app.config import settings
from app.database import SessionLocal
from app.services.sync_transfer import build_snapshot, replace_database_snapshot


class LaptopSyncAgent:
    def __init__(self, api_url: str | None = None):
        self.api_url = (api_url or settings.remote_api_url or "").rstrip("/")
        self.token = settings.api_access_token or ""
        self.worker_id = str(uuid4())
        self.poll_seconds = settings.laptop_agent_poll_seconds
        self.ready = False
        self.bootstrap_blocked = False

    def request(self, route: str, body: dict | None = None):
        if not self.api_url or not self.token:
            raise RuntimeError("Set REMOTE_API_URL and API_ACCESS_TOKEN in the laptop .env")
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            f"{self.api_url}{route}",
            data=data,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read() or b"{}")
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"Render API returned HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Render API is unreachable: {error.reason}") from error

    def bootstrap(self):
        with SessionLocal() as db:
            snapshot = build_snapshot(db)
        result = self.request("/sync/agent/bootstrap", {"snapshot": snapshot})
        self.ready = bool(result.get("ready"))
        self.bootstrap_blocked = result.get("reason") == "render_workspace_contains_data"
        if result.get("initialized"):
            print("Copied laptop PostgreSQL data into the empty Render workspace.", flush=True)
        elif self.bootstrap_blocked:
            print("Render already has data; refusing to overwrite it during first-time laptop setup.", flush=True)

    def send_heartbeat(self):
        self.request("/sync/agent/heartbeat?worker_id=" + self.worker_id, {})

    def claim(self):
        result = self.request("/sync/agent/claim?worker_id=" + self.worker_id, {})
        return result.get("operation")

    def process(self, operation: dict):
        operation_id = operation["operation_id"]
        if operation.get("entity_type") != "snapshot" or operation.get("operation") != "replace":
            raise ValueError("Unsupported pending operation; expected a full data snapshot")
        snapshot = json.loads(operation["payload"])
        with SessionLocal.begin() as db:
            replace_database_snapshot(db, snapshot)
        self.request(f"/sync/agent/{operation_id}/ack", {})
        print(f"Committed snapshot revision {snapshot.get('revision', '?')} to laptop PostgreSQL.", flush=True)

    def run(self):
        print("Laptop sync agent running. Press Ctrl+C to stop.", flush=True)
        try:
            self.bootstrap()
        except Exception as error:
            print(f"Initial Render workspace check failed: {error}", flush=True)
        while True:
            try:
                self.send_heartbeat()
                if not self.ready:
                    if not self.bootstrap_blocked:
                        try:
                            self.bootstrap()
                        except Exception as error:
                            print(f"Laptop database bootstrap will retry: {error}", flush=True)
                    if not self.ready:
                        time.sleep(max(self.poll_seconds, 30 if self.bootstrap_blocked else 5))
                        continue
                operation = self.claim()
                if operation:
                    try:
                        self.process(operation)
                    except Exception as error:
                        self.request(
                            f"/sync/agent/{operation['operation_id']}/fail",
                            {"error": str(error)[:4000]},
                        )
                        print(f"Snapshot remains queued for retry: {error}", flush=True)
                else:
                    time.sleep(self.poll_seconds)
            except KeyboardInterrupt:
                print("Laptop sync agent stopped.", flush=True)
                return
            except Exception as error:
                print(f"Laptop sync agent will retry: {error}", flush=True)
                time.sleep(min(max(self.poll_seconds, 1), 30))


if __name__ == "__main__":
    LaptopSyncAgent().run()
