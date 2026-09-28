# Field Visit Reporting

A single-user application for maintaining a doctor master from Excel, recording field visits, and analysing doctor, territory, product, and follow-up activity.

## Architecture

- **Hosted backend:** FastAPI, SQLAlchemy 2, Alembic, SQLite on a Render persistent disk
- **Permanent laptop database:** PostgreSQL, written by the laptop sync agent only after a requested flush
- **Frontend:** React, TypeScript, Vite, hosted on Netlify
- **Data model:** doctor master data is independent from transactional visit data. Analytics are SQL-derived and are never stored as counters.
- **Import:** Excel files are previewed and mapped before confirmation. Unknown columns are retained in `Doctor.extra_data`; repeated imports use conservative matching.
- **Staging:** the hosted application reads and writes its SQLite workspace. Pressing **Flush to laptop** creates a durable snapshot request; the laptop agent copies that snapshot into PostgreSQL.

The Render SQLite database is the live working copy, so the site continues to browse and report data after a flush. The SQLite queue contains the pending snapshot request. The laptop agent replaces the laptop's business tables inside one PostgreSQL transaction; only after that transaction commits does it acknowledge the request, which removes the queued snapshot. Retrying a snapshot is safe because applying it replaces the same tables with the same full data set.

Render's SQLite workspace and pending flush requests must be on a persistent disk. If the laptop is offline, the live site continues storing data on that disk and keeps flush requests queued. PostgreSQL is updated when the laptop agent reconnects.

## Development

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD in .env.
docker compose up -d db
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/uvicorn app.main:app --app-dir backend --reload
npm install --prefix frontend
npm run dev --prefix frontend
```

The connection is configured with `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`. `DATABASE_URL` remains an optional full-URL override for tests or unusual deployments. PostgreSQL can therefore move to another machine later without frontend changes.

## Deploy Render backend and Netlify frontend

1. Create a Render Blueprint from `render.yaml`. It deploys the FastAPI backend, attaches a persistent disk at `/var/data`, and keeps the SQLite workspace at `/var/data/field_reports.db`. A persistent disk is required; Render only preserves files under the disk mount path, and services with a disk cannot scale to multiple instances. Render requires a paid compute plan for persistent disks. [Render disk docs](https://render.com/docs/disks), [Blueprint disk settings](https://render.com/docs/blueprint-spec).
2. Deploy the frontend on Netlify from this repository. `netlify.toml` sets `frontend/dist` as the publish directory and includes the React route rewrite. Set Netlify build environment variable `VITE_API_URL` to `https://<your-render-service>.onrender.com/api`, then redeploy the frontend.
3. In Render, set `CORS_ORIGINS` to the exact Netlify site origin, for example `https://your-site.netlify.app`. Keep the generated Render `API_ACCESS_TOKEN` private. The health check at `/api/health` does not need the key.
4. On your laptop, set `REMOTE_API_URL=https://<your-render-service>.onrender.com/api` in `.env`. Copy the Render `API_ACCESS_TOKEN` into this laptop `.env` as well. Keep the local `POSTGRES_*` values pointed at your laptop's PostgreSQL database.
5. Apply the PostgreSQL migrations locally, then start the connector from the project directory:

   ```bash
   .venv/bin/alembic -c backend/alembic.ini upgrade head
   PYTHONPATH=backend .venv/bin/python backend/scripts/laptop_sync_agent.py
   ```

   On first connection, the agent copies the current laptop PostgreSQL data into an empty Render SQLite workspace. Start this agent before adding data on the live site. It then stays connected outbound to Render, so you do not need to expose your laptop or PostgreSQL port to the internet.

   The hosted API rejects create, update, import, and delete requests until this first copy completes. That startup lock prevents new hosted records from being overwritten by the initial laptop-to-Render seed. If Render already has records during first setup, the agent refuses to replace them; stop and reconcile those records before initializing the connector.

When you press **Flush to laptop**, Render stores a snapshot request in SQLite. The agent claims it, replaces the corresponding records in laptop PostgreSQL in one transaction, commits, then acknowledges the request. If PostgreSQL or the network fails, it reports the failure and leaves the snapshot queued for retry. Once acknowledged, the queued snapshot is removed; the Render SQLite working data remains so the live site keeps working.

If the laptop is off, edits remain on Render's persistent SQLite disk and the button shows that a flush is queued. The laptop must be running the agent for PostgreSQL to receive them. The Render disk is required for this waiting period so a backend restart does not lose the staged workspace.

For an existing installation that previously stored application records in `dr_reporting.db`, run this once after the PostgreSQL migrations:

```bash
PYTHONPATH=backend .venv/bin/python backend/scripts/migrate_legacy_sqlite.py dr_reporting.db
```

The migration preserves IDs, copies tables in dependency order, skips conflicts, and can be run again safely.

Run backend tests with `.venv/bin/pytest backend/tests -q`.

Open `http://localhost:5173`. The Vite development server proxies `/api` to the backend on port 8000. API documentation is available at `http://localhost:8000/docs`.

The header shows whether the laptop agent is connected, a snapshot is being sent, changes need a flush, or data is synchronized. Click it to queue a full snapshot for laptop PostgreSQL. API checks are available at `GET /api/health/db` and `GET /api/sync/status`; `POST /api/sync/flush` requests a snapshot.

## Main workflows

- **Add visit:** dependent HQ → Area → Doctor selection with doctor activity context.
- **Multiple visits:** select several doctors in an area and atomically create an independent visit for each one.
- **Insights:** period comparisons, repeat activity, coverage, top doctors/products, weekday patterns, category coverage, and time-since-last-visit analysis.
- **Data management:** remove individual visits, doctors, products, or areas, or clear a complete data category using an exact confirmation phrase.

## Import safety

Uploading a workbook creates a preview only. Review the detected mapping, validation results, new and matched counts, and resolve every ambiguous row before confirmation. Confirmation never creates visits, never deletes doctors omitted from a workbook, and only replaces existing doctor fields when the imported cell is nonblank.
