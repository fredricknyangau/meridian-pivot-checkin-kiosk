import json
from unittest.mock import patch
import pytest
import asyncpg
import pika
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.services.checkin import CheckinService, CheckinPublishError


async def reset_db_and_queue():
    """Reset DB tables and purge RabbitMQ queue."""
    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        await conn.execute("DELETE FROM print_jobs;")
        await conn.execute("UPDATE attendees SET status = 'NOT_REQUESTED', updated_at = NOW();")
    finally:
        await conn.close()

    # Purge RabbitMQ queue
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
async def test_publish_print_job_success():
    """Verify first check-in publishes exactly 1 persistent message to RabbitMQ with correct payload."""
    await reset_db_and_queue()

    with TestClient(app) as client:
        res = client.post("/checkin/1")
        assert res.status_code == 200
        data = res.json()
        print_job_id = data["print_job_id"]

    # Retrieve message directly from RabbitMQ
    params = pika.URLParameters(settings.RABBITMQ_URL)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    
    method_frame, header_frame, body = channel.basic_get(queue=settings.RABBITMQ_QUEUE, auto_ack=True)
    assert method_frame is not None, "Expected 1 message in RabbitMQ queue"

    payload = json.loads(body.decode("utf-8"))
    assert payload["print_job_id"] == print_job_id
    assert payload["attendee_id"] == 1
    assert payload["attendee_name"] == "Attendee One"
    assert header_frame.delivery_mode == 2

    # Assert queue has no additional messages
    method_frame, _, _ = channel.basic_get(queue=settings.RABBITMQ_QUEUE, auto_ack=True)
    assert method_frame is None
    connection.close()


@pytest.mark.asyncio
async def test_duplicate_scan_does_not_publish():
    """Verify duplicate scan does NOT publish any additional RabbitMQ message."""
    await reset_db_and_queue()

    with TestClient(app) as client:
        # First scan
        res1 = client.post("/checkin/1")
        assert res1.status_code == 200

        # Drain the message from 1st scan
        params = pika.URLParameters(settings.RABBITMQ_URL)
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        method_frame, _, _ = channel.basic_get(queue=settings.RABBITMQ_QUEUE, auto_ack=True)
        assert method_frame is not None

        # Duplicate scan
        res2 = client.post("/checkin/1")
        assert res2.status_code == 200

        # Verify no 2nd message was published
        method_frame, _, _ = channel.basic_get(queue=settings.RABBITMQ_QUEUE, auto_ack=True)
        assert method_frame is None, "Duplicate scan published an unwanted extra message"
        connection.close()


@pytest.mark.asyncio
async def test_rabbitmq_publish_failure_handled():
    """Verify that if publishing fails, DB state is reverted and CheckinPublishError is raised."""
    await reset_db_and_queue()

    pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL)
    try:
        def failing_publisher(*args, **kwargs):
            raise RuntimeError("RabbitMQ connection lost")

        service = CheckinService(pool, publisher_func=failing_publisher)

        with pytest.raises(CheckinPublishError):
            await service.checkin_attendee(1)

        # Verify DB state compensation: attendee remains NOT_REQUESTED and 0 print_jobs created
        async with pool.acquire() as conn:
            status = await conn.fetchval("SELECT status FROM attendees WHERE id = 1;")
            assert status == "NOT_REQUESTED"

            job_count = await conn.fetchval("SELECT COUNT(*) FROM print_jobs WHERE attendee_id = 1;")
            assert job_count == 0
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_route_publish_failure_returns_500():
    """Verify API endpoint returns 500 Internal Server Error when publishing fails."""
    await reset_db_and_queue()

    with patch("app.services.checkin.publish_print_job_sync", side_effect=RuntimeError("Broker unavailable")):
        with TestClient(app) as client:
            res = client.post("/checkin/1")
            assert res.status_code == 500
            assert res.json()["detail"] == "Failed to enqueue print job"
