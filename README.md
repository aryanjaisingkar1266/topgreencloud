# TopGreenCloud

TopGreenCloud helps people compare cloud providers' sustainability information and, in later phases, estimate the footprint of their cloud usage from bills.

Stack: Next.js, TypeScript, FastAPI, PostgreSQL, SQLAlchemy, Alembic, and psycopg. Google Cloud deployment follows in later phases.

## Frontend

Requires Node.js 20.9 or newer.

```bash
cd frontend
npm ci
npm run dev
```

Open http://localhost:3000. Run `npm run build` to verify a production build.

## Backend

Requires Python 3.10 or newer.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

On Windows, activate with `.venv\Scripts\activate`. Check http://localhost:8000/health. Run `python -m unittest discover -s tests` to run the backend test.

## Database

Provision PostgreSQL separately and set `DATABASE_URL` in the process environment using the format in `.env.example`. No `.env` file is loaded automatically. Keep credentials out of Git and URL-encode special characters in passwords.

From `backend/` with the virtual environment active:

```bash
alembic upgrade head
alembic current
```

`alembic upgrade head --sql` renders PostgreSQL SQL without connecting. Use `alembic downgrade base` only on a disposable database: it deletes all six application tables and their data. The migration also uses Alembic's version-tracking table; it seeds no data. Additional revisions are written in `alembic/versions/`; no revision-generation template is included yet.

CO₂e fields are nullable estimates in kilograms; null means unknown, not zero. Metric values are text to support both numbers and commitments. Timestamps are timezone-aware; SQLAlchemy updates `updated_at` on ORM updates (raw SQL must set it explicitly). Foreign keys enforce bill/analysis ownership and cascade dependent database records on deletion; deleting stored files remains future application work. `/health` is independent of PostgreSQL.
