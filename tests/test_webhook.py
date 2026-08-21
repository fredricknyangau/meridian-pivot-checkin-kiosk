import json
import uuid
from unittest.mock import MagicMock, patch
import pytest
import asyncpg
import pika
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from simulated_printer import process_message


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
async def test_valid_webhook_confirmation():
    """Test valid webhook transitions attendee PENDING -> CHECKED_IN and completes print_job."""
    await reset_db_and_queue()

    with TestClient(app) as client:
        # Step 1: Initial check-in
        checkin_res = client.post("/checkin/1")
        assert checkin_res.status_code == 200
        checkin_data = checkin_res.json()
        print_job_id = checkin_data["print_job_id"]
        assert checkin_data["status"] == "PENDING"

        # Step 2: Post print confirmation webhook
        webhook_payload = {
            "print_job_id": print_job_id,
            "result": "success"
        }
        webhook_res = client.post("/webhook/print-confirmation", json=webhook_payload)
        assert webhook_res.status_code == 200
        webhook_data = webhook_res.json()
        assert webhook_data["status"] == "success"
        assert webhook_data["message"] == "Attendee checked in successfully"
        assert webhook_data["attendee_id"] == 1

        # Step 3: Verify GET /attendees/1
        get_res = client.get("/attendees/1")
        assert get_res.status_code == 200
        assert get_res.json()["status"] == "CHECKED_IN"

    # Step 4: Verify DB state directly
    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        job = await conn.fetchrow("SELECT status, completed_at FROM print_jobs WHERE id = $1;", uuid.UUID(print_job_id))
        assert job["status"] == "COMPLETED"
        assert job["completed_at"] is not None

        attendee = await conn.fetchrow("SELECT status FROM attendees WHERE id = 1;")
        assert attendee["status"] == "CHECKED_IN"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_unknown_print_job_id_webhook():
    """Test webhook with non-existent print_job_id returns 404."""
    await reset_db_and_queue()

    unknown_uuid = str(uuid.uuid4())
    with TestClient(app) as client:
        webhook_res = client.post(
            "/webhook/print-confirmation",
            json={"print_job_id": unknown_uuid, "result": "success"}
        )
        assert webhook_res.status_code == 404
        assert webhook_res.json()["detail"] == "Print job not found"

    # Verify no attendees were modified
    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        statuses = await conn.fetch("SELECT status FROM attendees;")
        assert all(s["status"] == "NOT_REQUESTED" for s in statuses)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_duplicate_webhook_confirmation():
    """Test idempotent duplicate webhook for already CHECKED_IN attendee returns 200 success."""
    await reset_db_and_queue()

    with TestClient(app) as client:
        # First scan
        res = client.post("/checkin/1")
        print_job_id = res.json()["print_job_id"]

        # First webhook confirmation
        wh1 = client.post("/webhook/print-confirmation", json={"print_job_id": print_job_id, "result": "success"})
        assert wh1.status_code == 200
        assert wh1.json()["message"] == "Attendee checked in successfully"

        # Duplicate webhook confirmation
        wh2 = client.post("/webhook/print-confirmation", json={"print_job_id": print_job_id, "result": "success"})
        assert wh2.status_code == 200
        assert wh2.json()["status"] == "success"
        assert wh2.json()["message"] == "Webhook already processed"

    # Verify state remains CHECKED_IN and print_jobs row count stays 1
    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        attendee_status = await conn.fetchval("SELECT status FROM attendees WHERE id = 1;")
        assert attendee_status == "CHECKED_IN"

        count = await conn.fetchval("SELECT COUNT(*) FROM print_jobs WHERE attendee_id = 1;")
        assert count == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_simulated_printer_worker_flow():
    """Test simulated printer worker consuming RabbitMQ message and calling webhook."""
    await reset_db_and_queue()

    with TestClient(app) as client:
        # Check-in publishes message to RabbitMQ
        res = client.post("/checkin/1")
        print_job_id = res.json()["print_job_id"]

        # Pull message from RabbitMQ
        params = pika.URLParameters(settings.RABBITMQ_URL)
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        method_frame, header_frame, body = channel.basic_get(queue=settings.RABBITMQ_QUEUE, auto_ack=False)
        assert method_frame is not None

        # Execute process_message with TestClient dispatch
        mock_channel = MagicMock()
        with patch("simulated_printer.httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            process_message(mock_channel, method_frame, header_frame, body)

            # Verify HTTP POST payload sent to webhook
            mock_post.assert_called_once()
            called_url, called_kwargs = mock_post.call_args
            assert called_url[0] == f"{settings.API_BASE_URL}/webhook/print-confirmation"
            assert called_kwargs["json"]["print_job_id"] == print_job_id
            assert called_kwargs["json"]["result"] == "success"

            # Verify manual basic_ack was called on channel
            mock_channel.basic_ack.assert_called_once_with(delivery_tag=method_frame.delivery_tag)

        connection.close()
