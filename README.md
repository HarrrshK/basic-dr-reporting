# Field Visit Reporting

A single-user application for managing a doctor master, recording field visits, and analysing doctor, territory, product, and follow-up activity.

## Production architecture

```text
Netlify React app → Render FastAPI → Supabase PostgreSQL (source of truth)
                                       ├── laptop PostgreSQL backup agent
                                       └── daily JSON → optional S3-compatible storage
```

The FastAPI service reads and writes Supabase directly. The laptop is never on the live application's request path. Turning it off only pauses the laptop backup. The agent pulls ordered database changes in batches, commits them to laptop PostgreSQL, and advances its cursor only after the local transaction succeeds. Stable IDs and PostgreSQL upserts make retries safe. PostgreSQL triggers record committed inserts, updates, and deletes in the change feed, including bulk data-management operations.

The existing Excel importer still updates Doctor Master only. Visits remain separate records linked to doctors. The database schema and backup format include areas, products, imports, doctors, visits, and visit-product relationships.

## Create a Supabase database

1. Create a Supabase project and set its database password.
2. In **Connect**, copy the PostgreSQL session-pooler or direct connection string. Use the host, port, username, and database shown by Supabase. Put the URL in a private `.env` as `DATABASE_URL`; do not commit it.
3. Supabase connections require TLS. This application adds `sslmode=require` when it is missing from a PostgreSQL URL. [Supabase connection documentation](https://supabase.com/docs/guides/database/connecting-to-postgres).

If the database password contains reserved URL characters, URL-encode them before saving the connection string.

## Deploy the API to Render

1. Push the repository to GitHub and create a Render Blueprint using `render.yaml`.
2. On the Render service, set `DATABASE_URL` to the Supabase PostgreSQL URL. Browser API routes are public for this single-user app. Keep `API_ACCESS_TOKEN` as a Render-only secret if you use the laptop backup agent or scheduled JSON backup; never add it to Netlify.
3. Deploy. The start command runs Alembic migrations before starting FastAPI. There is no production SQLite file or persistent disk. A Render restart does not remove application records because they live in Supabase.
4. Confirm `https://<render-service>.onrender.com/api/health` returns `{"status":"ok"}`. `GET /api/health/db` checks the actual PostgreSQL connection and is available without a browser token.

The Render free web service can spin down while idle, so the first request after a quiet period may take longer. Its local filesystem is not used for report storage.

## Deploy the frontend to Netlify

1. Import the same GitHub repository as a Netlify site.
2. Set the build environment variable `VITE_API_URL` to `https://<render-service>.onrender.com/api`.
3. Redeploy both services. The API allows cross-origin requests from any site and does not use cookie credentials.
4. Open the site. It talks to FastAPI without a browser-held API token. The browser never receives the database URL or backup agent token.

## Move existing SQLite data to Supabase

Keep the original SQLite file unchanged. The repository's `dr_reporting.db` was checked and migrated into a separate temporary database during implementation: it contained 133 doctors, 8 areas, 3 import batches, and no visits or products. The migration preserved all row IDs and passed its relationship checks.

After Render or a local Alembic command has upgraded Supabase to the current migration:

```bash
cp .env.example .env
# Edit .env and set DATABASE_URL to Supabase; API_ACCESS_TOKEN is only needed for backup agents.
.venv/bin/alembic -c backend/alembic.ini upgrade head
PYTHONPATH=backend .venv/bin/python backend/scripts/migrate_legacy_sqlite.py dr_reporting.db
```

The migration validates source tables and primary keys, copies tables in foreign-key order, preserves IDs/timestamps/JSON, and runs as one PostgreSQL transaction. If a target ID already exists with different field values, it stops and rolls back instead of overwriting it. Re-running identical data is safe. Its output includes source, inserted, already-present, target, and relationship-check counts. Compare `source_rows` with the corresponding target totals in the report before using the site. Never remove the original `.db` file until you have verified the Supabase data.

For a SQLite file copied from an old Render disk or another location, pass that file's path as the final argument. The migration intentionally does not remove or modify its source.

## Laptop PostgreSQL backup

The laptop database is a backup copy, not the main database. Create it and set these local `.env` values:

```env
DATABASE_URL=<Supabase PostgreSQL URL>
REMOTE_API_URL=https://<render-service>.onrender.com/api
API_ACCESS_TOKEN=<Render API access token>
BACKUP_DATABASE_URL=postgresql://postgres:<local-password>@127.0.0.1:5432/dr_reporting
BACKUP_INTERVAL_SECONDS=300
BACKUP_BATCH_SIZE=500
BACKUP_LOCAL_DIR=backups
```

Apply the schema to the laptop backup database and start its outbound-only agent:

```bash
DATABASE_URL="$BACKUP_DATABASE_URL" .venv/bin/alembic -c backend/alembic.ini upgrade head
PYTHONPATH=backend .venv/bin/python backend/scripts/laptop_backup_agent.py
```

On first start, the agent upserts a cloud snapshot into laptop PostgreSQL and records its change cursor. It then asks Supabase for up to 500 new changes every 300 seconds by default. Change the interval with `BACKUP_INTERVAL_SECONDS`. If the laptop is off, Supabase continues serving and storing reports. When the agent returns, it resumes from the last acknowledged cursor. Replayed changes use stable primary keys and are safe to apply again.

The header has one **Backup to Laptop** button. It requests an immediate backup on the agent's next poll; it does not claim success until the agent commits and acknowledges. The status reports laptop connectivity, pending changes, and the last confirmed backup time. If the agent is off, the request remains pending and production is unaffected.

## Daily JSON and external storage

`GET /api/backup/export` downloads a versioned JSON document for all application records. It works while the laptop is off and requires the private backup token. `POST /api/backup/import` validates the version, table shapes, IDs, types, and relationships, then restores in a transaction using primary-key upserts. Re-importing the same file is safe. Invalid input or a constraint failure rolls the whole import back. Normal reporting routes do not require the token.

The laptop agent also saves a dated JSON copy under `BACKUP_LOCAL_DIR` when it is running. For a daily copy independent of the laptop, the repository includes a GitHub Actions scheduled workflow. Configure these Render environment values to enable S3-compatible uploads:

```text
BACKUP_PROVIDER=s3
BACKUP_BUCKET=<bucket>
BACKUP_PREFIX=field-reports
AWS_ACCESS_KEY_ID=<key>
AWS_SECRET_ACCESS_KEY=<secret>
AWS_REGION=<region>
AWS_ENDPOINT_URL=<optional S3-compatible endpoint>
```

Then add GitHub Actions secret `BACKUP_API_URL` (the Render API URL ending in `/api`) and secret `API_ACCESS_TOKEN` (the same Render token). Add repository variable `BACKUP_PROVIDER=s3`. The scheduled workflow exports JSON from FastAPI and asks the backend to upload it to the configured bucket each day; it can also be run manually from the Actions tab. The laptop agent uses the same configured backend upload when it is running.

Without all required S3 settings, the UI explicitly says **External backup not configured**. JSON remains downloadable from the API and saved locally by the agent, but a file on Render's temporary filesystem is not an off-site backup. Treat the S3 bucket as the separate disaster-recovery copy and test restoring a downloaded JSON file periodically.

## Development

```bash
cp .env.example .env
# Set DATABASE_URL to a local PostgreSQL database or a Supabase development project.
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/uvicorn app.main:app --app-dir backend --reload
npm install --prefix frontend
npm run dev --prefix frontend
```

Set `API_ACCESS_TOKEN` only if using the laptop backup agent or external backup workflow. The Vite server serves the UI at `http://localhost:5173`; the API docs are at `http://localhost:8000/docs`. CORS accepts requests from any origin.

To run laptop PostgreSQL locally with the included Compose file, first set `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `BACKUP_DATABASE_URL` in `.env`, then run `docker compose up -d db`.

## Reports and call exports

The Reports page provides date, HQ, area, category, doctor, product, purpose, outcome, and follow-up filters. It derives visit trends, doctor frequency and gaps, coverage, territory activity, product activity, call purpose/outcome mix, and follow-up status from the doctor and visit records. Filtered call reports can be downloaded as CSV or Excel (`.xlsx`) from Reports or Visit History. Doctor CSV exports preserve the selected doctor filters and search term.

## Validation

```bash
DATABASE_URL='sqlite://' PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
npm run build --prefix frontend
```

SQLite is used by automated tests only. Production tables are created and changed by Alembic migrations on PostgreSQL.
