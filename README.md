# Aarogya Bandhu

Post-discharge IVR follow-up for Karnataka government hospitals.

## Local setup

```bash
git clone https://github.com/POTATO-VE1/Arogya-Bandhu.git
cd aarogya-bandhu

# backend
cd backend
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env: set SECRET_KEY, ADMIN_PASSWORD, PUBLIC_BASE_URL
cd ..
./venv/bin/python -m app.scripts.seed_demo --reset --with-demo-data

# run backend
cd backend
./venv/bin/uvicorn app.main:app --port 8000
```

```bash
# in another terminal: frontend
cd aarogya-bandhu/frontend
npm install
npm run dev
# open http://localhost:5173
# login: admin / <ADMIN_PASSWORD from .env>
```

## Tests

```bash
cd backend
./venv/bin/pytest -q
```
