"""Pull committed production changes from Supabase into laptop PostgreSQL."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import BackupAgentState
from app.services.backup_archive import apply_backup_changes, restore_backup_document


class LaptopBackupAgent:
    def __init__(self):
        self.api_url = (settings.remote_api_url or "").rstrip("/")
        self.token = settings.api_access_token or ""
        self.interval = max(10, settings.backup_interval_seconds)
        self.batch_size = min(max(settings.backup_batch_size, 1), 1000)
        if not self.api_url or not self.token:
            raise RuntimeError("Set REMOTE_API_URL and API_ACCESS_TOKEN in the laptop .env")
        self.engine = create_engine(settings.backup_sqlalchemy_url, pool_pre_ping=True,
                                    pool_recycle=300, pool_size=2, max_overflow=0)
        self.last_error: str | None = None
        self.last_archive_date: str | None = None

    def request(self, route: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            f"{self.api_url}{route}", data=data,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST" if body is not None else "GET",
        )
        try:
            with urlopen(request, timeout=45) as response:
                return json.loads(response.read() or b"{}")
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"Production backup API returned HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Production backup API is unreachable: {error.reason}") from error

    @staticmethod
    def save_checkpoint(connection, cursor: int, complete: bool):
        table = BackupAgentState.__table__
        result = connection.execute(update(table).where(table.c.id == 1).values(
            cursor=cursor, bootstrap_complete=complete,
        ))
        if not result.rowcount:
            connection.execute(insert(table).values(
                id=1, cursor=cursor, bootstrap_complete=complete,
            ))

    def local_checkpoint(self) -> tuple[bool, int]:
        with Session(self.engine) as db:
            state = db.get(BackupAgentState, 1)
            return (bool(state and state.bootstrap_complete), int(state.cursor or 0) if state else 0)

    def apply_document(self, document: dict, cursor: int) -> None:
        with self.engine.begin() as connection:
            restore_backup_document(connection, document)
            self.save_checkpoint(connection, cursor, True)

    def apply_changes(self, changes: list[dict], cursor: int) -> None:
        with self.engine.begin() as connection:
            apply_backup_changes(connection, changes)
            self.save_checkpoint(connection, cursor, True)

    def heartbeat(self):
        self.request("/backup/agent/heartbeat", {"last_error": self.last_error})

    def bootstrap(self):
        response = self.request("/backup/agent/bootstrap?force=true")
        if not response.get("required"):
            return int(response.get("cursor", 0)), False
        cursor = int(response["cursor"])
        self.apply_document(response["document"], cursor)
        result = self.request("/backup/agent/bootstrap/ack", {"cursor": response["cursor"]})
        print(f"Initial laptop backup committed through change {result['cursor']}.", flush=True)
        return result["cursor"], True

    def pull_changes(self, cursor: int, requested: bool) -> tuple[int, bool]:
        changed = False
        while True:
            query = urlencode({"after_id": cursor, "limit": self.batch_size})
            response = self.request(f"/backup/agent/changes?{query}")
            changes = response.get("changes", [])
            if changes:
                next_cursor = int(response["next_cursor"])
                self.apply_changes(changes, next_cursor)
                cursor = next_cursor
                self.request("/backup/agent/ack", {"cursor": cursor})
                changed = True
                print(f"Laptop backup committed through change {cursor}.", flush=True)
            if not response.get("has_more"):
                break
        if requested and not changed:
            self.request("/backup/agent/ack", {"cursor": cursor})
        return cursor, changed

    def write_daily_json(self) -> None:
        today = date.today().isoformat()
        folder = Path(settings.backup_local_dir)
        destination = folder / f"{today}.json"
        if not destination.exists():
            document = self.request("/backup/export")
            folder.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            temporary.replace(destination)
        document = json.loads(destination.read_text(encoding="utf-8"))
        status = self.request("/backup/status")
        if status.get("external_backup_configured") and self.last_archive_date != today:
            result = self.request("/backup/archive", {"document": document})
            print(f"Daily JSON uploaded to configured object storage: {result['object_key']}", flush=True)
            self.last_archive_date = today
        elif not getattr(self, "unconfigured_notice_date", None) == today:
            print(f"Daily JSON saved locally: {destination}", flush=True)
            print("External backup not configured on the production API.", flush=True)
            self.unconfigured_notice_date = today

    def run_once(self, cursor: int = 0) -> int:
        self.heartbeat()
        status = self.request("/backup/agent/status")
        local_ready, local_cursor = self.local_checkpoint()
        server_cursor = int(status.get("cursor", 0))
        if not local_ready or server_cursor > local_cursor:
            local_cursor, _ = self.bootstrap()
            status = self.request("/backup/agent/status")
        elif not status.get("bootstrap_complete"):
            self.request("/backup/agent/bootstrap/ack", {"cursor": local_cursor})
            status = self.request("/backup/agent/status")
        elif local_cursor > server_cursor:
            self.request("/backup/agent/ack", {"cursor": local_cursor})
            status = self.request("/backup/agent/status")
        if status.get("bootstrap_complete"):
            local_cursor, _ = self.pull_changes(local_cursor, bool(status.get("requested")))
        self.write_daily_json()
        self.last_error = None
        return local_cursor

    def run(self):
        print(f"Laptop backup agent running; checks production every {self.interval} seconds.", flush=True)
        cursor = 0
        while True:
            try:
                cursor = self.run_once(cursor)
            except KeyboardInterrupt:
                print("Laptop backup agent stopped.", flush=True)
                self.engine.dispose()
                return
            except Exception as error:
                self.last_error = f"{type(error).__name__} during laptop backup"[:1000]
                print(f"Backup attempt failed; production data is unaffected: {self.last_error}", flush=True)
            time.sleep(self.interval)


if __name__ == "__main__":
    LaptopBackupAgent().run()
