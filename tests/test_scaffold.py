import uuid
import pytest
import asyncpg
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings


async def reset_scaffold_db():
    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        await conn.execute("DELETE FROM print_jobs;")
        await conn.execute("UPDATE attendees SET status = 'NOT_REQUESTED', updated_at = NOW();")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_app_imports_and_routes():
    """Verify application imports and endpoints respond correctly."""
    await reset_scaffold_db()
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        attendees_resp = client.get("/checkin/attendees")
        assert attendees_resp.status_code == 200
        data = attendees_resp.json()
        assert len(data) == 3
        assert [a["name"] for a in data] == ["Attendee One", "Attendee Two", "Attendee Three"]
        assert all(a["status"] == "NOT_REQUESTED" for a in data)


@pytest.mark.asyncio
async def test_database_connection_and_seed_data():
    """Verify database connection pool and 3 seeded attendees with NOT_REQUESTED status."""
    await reset_scaffold_db()
    pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, name, status FROM attendees ORDER BY id ASC;")
            assert len(rows) == 3

            names = [r["name"] for r in rows]
            statuses = [r["status"] for r in rows]

            assert names == ["Attendee One", "Attendee Two", "Attendee Three"]
            assert statuses == ["NOT_REQUESTED", "NOT_REQUESTED", "NOT_REQUESTED"]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_print_jobs_partial_unique_index():
    """Verify that an attendee cannot have more than one PENDING print job."""
    await reset_scaffold_db()
    pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            attendee_id = await conn.fetchval("SELECT id FROM attendees ORDER BY id ASC LIMIT 1;")
            job_id_1 = uuid.uuid4()
            job_id_2 = uuid.uuid4()

            # Clean up any leftover test jobs
            await conn.execute("DELETE FROM print_jobs WHERE attendee_id = $1;", attendee_id)

            # First print job insert should succeed
            await conn.execute(
                "INSERT INTO print_jobs (id, attendee_id, status) VALUES ($1, $2, 'PENDING');",
                job_id_1, attendee_id
            )

            # Second PENDING print job for the same attendee should raise UniqueViolationError
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO print_jobs (id, attendee_id, status) VALUES ($1, $2, 'PENDING');",
                    job_id_2, attendee_id
                )

            # Cleanup test print job
            await conn.execute("DELETE FROM print_jobs WHERE attendee_id = $1;", attendee_id)
    finally:
        await pool.close()
