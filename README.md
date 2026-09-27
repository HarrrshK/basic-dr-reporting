# Field Visit Reporting

A single-user application for maintaining a doctor master from Excel, recording field visits, and analysing doctor, territory, product, and follow-up activity.

## Architecture

- **Backend:** FastAPI, SQLAlchemy 2, Alembic, PostgreSQL (SQLite is supported for tests)
- **Frontend:** React, TypeScript, Vite
- **Data model:** doctor master data is independent from transactional visit data. Analytics are SQL-derived and are never stored as counters.
- **Import:** Excel files are previewed and mapped before confirmation. Unknown columns are retained in `Doctor.extra_data`; repeated imports use conservative matching.

## Development

```bash
cp .env.example .env
docker compose up -d db
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/dr_reporting .venv/bin/alembic -c backend/alembic.ini upgrade head
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/dr_reporting .venv/bin/uvicorn app.main:app --app-dir backend --reload
npm install --prefix frontend
npm run dev --prefix frontend
```

Run backend tests with `.venv/bin/pytest backend/tests -q`.

Open `http://localhost:5173`. The Vite development server proxies `/api` to the backend on port 8000. API documentation is available at `http://localhost:8000/docs`.

## Import safety

Uploading a workbook creates a preview only. Review the detected mapping, validation results, new and matched counts, and resolve every ambiguous row before confirmation. Confirmation never creates visits, never deletes doctors omitted from a workbook, and only replaces existing doctor fields when the imported cell is nonblank.
