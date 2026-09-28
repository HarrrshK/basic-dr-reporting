"""Fetch today's validated archive and ask the production API to store it in S3."""

from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request(url: str, token: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
    }, method=method)
    last_error = None
    for attempt in range(10):
        try:
            with urlopen(req, timeout=45) as response:
                return json.loads(response.read())
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:250]
            if error.code not in {502, 503, 504}:
                raise RuntimeError(f"Backup API returned HTTP {error.code}: {detail}") from error
            last_error = detail
        except URLError as error:
            last_error = str(error.reason)
        time.sleep(min(15, attempt + 1))
    raise RuntimeError(f"Backup API did not become ready: {last_error}")


def main():
    base_url = os.environ["BACKUP_API_URL"].rstrip("/")
    token = os.environ["API_ACCESS_TOKEN"]
    document = request(f"{base_url}/backup/export", token)
    if (document.get("export_version") != 1 or not document.get("export_date")
            or not isinstance(document.get("records"), dict)):
        raise RuntimeError("Production API returned an invalid JSON backup document")
    result = request(f"{base_url}/backup/archive", token, "POST", {"document": document})
    if not result.get("uploaded"):
        raise RuntimeError("Production API did not confirm external backup upload")
    print(f"Uploaded daily JSON backup {result['export_date']} to {result['object_key']}.")


if __name__ == "__main__":
    main()
