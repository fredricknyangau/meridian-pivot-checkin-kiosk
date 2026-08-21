import asyncio
import pytest
import asyncpg
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.services.checkin import CheckinService


async def reset_db():
    """Reset attendees to NOT_REQUESTED and delete print_jobs."""
    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        await conn.execute("DELETE FROM print_jobs;")
        await conn.execute("UPDATE attendees SET status = 'NOT_REQUESTED', updated_at = NOW();")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_first_checkin_success():
    """Test first scan transitions NOT_REQUESTED to PENDING and returns print_job_id."""
    await reset_db()
    with TestClient(app) as client:
        # First scan
        res = client.post("/checkin/1")
        assert res.status_code == 200
        data = res.json()
        assert data["attendee_id"] == 1
        assert data["status"] == "PENDING"
        assert "print_job_id" in data
        assert data["print_job_id"] is not None
        assert data.get("message") is None

        # Verify GET /attendees/1
        get_res = client.get("/attendees/1")
        assert get_res.status_code == 200
        get_data = get_res.json()
        assert get_data["attendee_id"] == 1
        assert get_data["name"] == "Attendee One"
        assert get_data["status"] == "PENDING"


@pytest.mark.asyncio
async def test_duplicate_pending_scan():
    """Test duplicate scan returns PENDING state, same print_job_id, message, and no 2nd row."""
    await reset_db()
    with TestClient(app) as client:
        # First scan
        res1 = client.post("/checkin/1")
        assert res1.status_code == 200
        data1 = res1.json()
        first_print_job_id = data1["print_job_id"]

        # Duplicate scan
        res2 = client.post("/checkin/1")
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["attendee_id"] == 1
        assert data2["status"] == "PENDING"
        assert data2["print_job_id"] == first_print_job_id
        assert data2["message"] == "Check-in already in progress"

    # Verify database has exactly 1 print job for attendee 1
    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM print_jobs WHERE attendee_id = 1;"
        )
        assert count == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_already_checked_in_attendee():
    """Test scan for an attendee who is already CHECKED_IN."""
    await reset_db()
    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        await conn.execute("UPDATE attendees SET status = 'CHECKED_IN' WHERE id = 1;")
    finally:
        await conn.close()

    with TestClient(app) as client:
        res = client.post("/checkin/1")
        assert res.status_code == 200
        data = res.json()
        assert data["attendee_id"] == 1
        assert data["status"] == "CHECKED_IN"
        assert data["message"] == "Attendee already checked in"


@pytest.mark.asyncio
async def test_unknown_attendee_404():
    """Test checkin and lookup for non-existent attendee return 404."""
    await reset_db()
    with TestClient(app) as client:
        res_post = client.post("/checkin/99999")
        assert res_post.status_code == 404
        assert res_post.json()["detail"] == "Attendee not found"

        res_get = client.get("/attendees/99999")
        assert res_get.status_code == 404
        assert res_get.json()["detail"] == "Attendee not found"


@pytest.mark.asyncio
async def test_concurrent_checkin_attempts():
    """Verify concurrent requests on NOT_REQUESTED attendee result in exactly one successful claim."""
    await reset_db()
    pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL)
    try:
        service = CheckinService(pool)

        # Run two concurrent check-in operations on attendee 2
        results = await asyncio.gather(
            service.checkin_attendee(2),
            service.checkin_attendee(2)
        )

        statuses = [r.status for r in results if r]
        messages = [r.message for r in results if r]
        print_job_ids = [r.print_job_id for r in results if r]

        assert len(results) == 2
        assert statuses == ["PENDING", "PENDING"]
        # Exactly one request produced the new claim (no message), and one received duplicate message
        assert None in messages
        assert "Check-in already in progress" in messages
        # Both share the same print_job_id (since 2nd fetch retrieves the 1st job's ID)
        assert print_job_ids[0] == print_job_ids[1]

        # Check database: exactly 1 print job record created
        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM print_jobs WHERE attendee_id = 2;")
            assert count == 1
    finally:
        await pool.close()
