import uuid
import pytest
import asyncpg
import pika
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings


async def reset_db_and_queue():
    """Reset DB tables and purge RabbitMQ queue."""
    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        await conn.execute("DELETE FROM print_jobs;")
        await conn.execute("UPDATE attendees SET status = 'NOT_REQUESTED', updated_at = NOW();")
    finally:
        await conn.close()

    try:
        params = pika.URLParameters(settings.RABBITMQ_URL)
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.queue_declare(
            queue=settings.RABBITMQ_QUEUE,
            durable=True,
            arguments={"x-queue-type": "quorum"}
        )
        channel.queue_purge(queue=settings.RABBITMQ_QUEUE)
        connection.close()
    except Exception as e:
        print(f"Warning: Failed to purge RabbitMQ queue: {e}")


@pytest.mark.asyncio
async def test_full_end_to_end_checkin_flow():
    """End-to-end correctness test for full Solstice Events check-in flow."""
    await reset_db_and_queue()

    with TestClient(app) as client:
        # Step 1: Check in attendee 1
        res1 = client.post("/checkin/1")
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["attendee_id"] == 1
        assert data1["status"] == "PENDING"
        job_1_id = data1["print_job_id"]
        assert job_1_id is not None
        print("PASS — first check-in")

        # Step 2: Immediately check in attendee 1 again
        res1_dup = client.post("/checkin/1")
        assert res1_dup.status_code == 200
        data1_dup = res1_dup.json()
        assert data1_dup["attendee_id"] == 1
        assert data1_dup["status"] == "PENDING"
        assert data1_dup["print_job_id"] == job_1_id
        assert data1_dup["message"] == "Check-in already in progress"
        print("PASS — duplicate scan prevented")

        # Verify DB print_jobs count for attendee 1 is 1
        conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
        try:
            count1 = await conn.fetchval("SELECT COUNT(*) FROM print_jobs WHERE attendee_id = 1;")
            assert count1 == 1
        finally:
            await conn.close()
        print("PASS — no second print job")

        # Step 3: Check in attendee 2
        res2 = client.post("/checkin/2")
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["attendee_id"] == 2
        assert data2["status"] == "PENDING"
        job_2_id = data2["print_job_id"]
        assert job_2_id is not None
        print("PASS — attendee 2 check-in")

        # Step 4: Check in attendee 3
        res3 = client.post("/checkin/3")
        assert res3.status_code == 200
        data3 = res3.json()
        assert data3["attendee_id"] == 3
        assert data3["status"] == "PENDING"
        job_3_id = data3["print_job_id"]
        assert job_3_id is not None
        print("PASS — attendee 3 check-in")

        # Step 5: Capture & verify all 3 print_job_ids are distinct
        distinct_jobs = {job_1_id, job_2_id, job_3_id}
        assert len(distinct_jobs) == 3

        # Step 6 & 7: Deliver webhook confirmations out of order (3, then 1, then 2)
        # Deliver for attendee 3
        wh_res3 = client.post("/webhook/print-confirmation", json={"print_job_id": job_3_id, "result": "success"})
        assert wh_res3.status_code == 200
        assert wh_res3.json()["status"] == "success"
        get3 = client.get("/attendees/3").json()
        assert get3["status"] == "CHECKED_IN"

        # Deliver for attendee 1
        wh_res1 = client.post("/webhook/print-confirmation", json={"print_job_id": job_1_id, "result": "success"})
        assert wh_res1.status_code == 200
        assert wh_res1.json()["status"] == "success"
        get1 = client.get("/attendees/1").json()
        assert get1["status"] == "CHECKED_IN"

        # Deliver for attendee 2
        wh_res2 = client.post("/webhook/print-confirmation", json={"print_job_id": job_2_id, "result": "success"})
        assert wh_res2.status_code == 200
        assert wh_res2.json()["status"] == "success"
        get2 = client.get("/attendees/2").json()
        assert get2["status"] == "CHECKED_IN"

        print("PASS — out-of-order confirmations")

        # Step 8: Deliver attendee 1's webhook a second time
        wh_res1_dup = client.post("/webhook/print-confirmation", json={"print_job_id": job_1_id, "result": "success"})
        assert wh_res1_dup.status_code == 200
        assert wh_res1_dup.json()["status"] == "success"
        assert wh_res1_dup.json()["message"] == "Webhook already processed"

        get1_after_dup = client.get("/attendees/1").json()
        assert get1_after_dup["status"] == "CHECKED_IN"
        print("PASS — duplicate webhook idempotency")

    # Step 9: Final Database State Verification
    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        attendees = await conn.fetch("SELECT id, status FROM attendees ORDER BY id ASC;")
        assert len(attendees) == 3
        assert all(a["status"] == "CHECKED_IN" for a in attendees)
        print("PASS — final state: 3 CHECKED_IN")

        jobs = await conn.fetch("SELECT id, attendee_id, status FROM print_jobs;")
        assert len(jobs) == 3
        assert all(j["status"] == "COMPLETED" for j in jobs)
        unique_job_ids_in_db = {str(j["id"]) for j in jobs}
        assert len(unique_job_ids_in_db) == 3
        print("PASS — exactly 3 print jobs")
    finally:
        await conn.close()
