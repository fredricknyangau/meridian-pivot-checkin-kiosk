# Solstice Events Check-In Kiosk Service

A minimal FastAPI microservice for event check-in badge processing at Solstice Events Co.

## Architecture & Domain Model

- **FastAPI**: Async HTTP endpoints
- **PostgreSQL & asyncpg**: Database state management using raw SQL
- **RabbitMQ & pika**: Asynchronous print request queue
- **State Flow**: `NOT_REQUESTED` -> `PENDING` -> `CHECKED_IN`

## Quick Start

### 1. Setup Virtual Environment
```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 2. Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

### 3. Run Migrations and Seed
```bash
uv run python scripts/seed_data.py
```

### 4. Run Application
```bash
uv run uvicorn app.main:app --reload --port 8000
```

### 5. Run Tests
```bash
uv run pytest tests/
```
