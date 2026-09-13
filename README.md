# TopGreenCloud

TopGreenCloud helps people compare cloud providers' sustainability information and, in later phases, estimate the footprint of their cloud usage from bills.

Stack: Next.js, TypeScript, FastAPI; PostgreSQL and Google Cloud deployment follow in later phases.

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
