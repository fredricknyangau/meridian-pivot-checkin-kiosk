import asyncio
import logging
import uuid
from typing import List, Optional, Callable
from uuid import UUID
import asyncpg
from app.messaging.rabbitmq import publish_print_job_sync, RabbitMQPublishError
from app.schemas.checkin import (
    AttendeeSchema,
    CheckinResponse,
    AttendeeDetailResponse,
    PrintConfirmationResponse,
)

logger = logging.getLogger(__name__)


class CheckinPublishError(Exception):
    """Raised when check-in database transition succeeded but RabbitMQ publication failed."""
    pass


class CheckinService:
    def __init__(self, db_pool: asyncpg.Pool, publisher_func: Optional[Callable] = None):
        self.db_pool = db_pool
        self.publisher_func = publisher_func or publish_print_job_sync

    async def get_all_attendees(self) -> List[AttendeeSchema]:
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, name, status, created_at, updated_at FROM attendees ORDER BY id ASC;")
            return [AttendeeSchema(**dict(r)) for r in rows]

    async def get_attendee_detail(self, attendee_id: int) -> Optional[AttendeeDetailResponse]:
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, status FROM attendees WHERE id = $1;",
                attendee_id
            )
            if not row:
                return None
            return AttendeeDetailResponse(
                attendee_id=row["id"],
                name=row["name"],
                status=row["status"]
            )

    async def checkin_attendee(self, attendee_id: int) -> Optional[CheckinResponse]:
        new_print_job_id = uuid.uuid4()
        updated_row = None

        async with self.db_pool.acquire() as conn:
            # 1. Atomic claim using UPDATE ... WHERE status = 'NOT_REQUESTED'
            async with conn.transaction():
                updated_row = await conn.fetchrow(
                    """
                    UPDATE attendees
                    SET status = 'PENDING',
                        updated_at = NOW()
                    WHERE id = $1
                      AND status = 'NOT_REQUESTED'
                    RETURNING id, name, status;
                    """,
                    attendee_id
                )

                if updated_row:
                    await conn.execute(
                        """
                        INSERT INTO print_jobs (id, attendee_id, status)
                        VALUES ($1, $2, 'PENDING');
                        """,
                        new_print_job_id,
                        attendee_id
                    )

            # 2. Transaction committed cleanly - attempt RabbitMQ publication
            if updated_row:
                try:
                    await asyncio.to_thread(
                        self.publisher_func,
                        str(new_print_job_id),
                        updated_row["id"],
                        updated_row["name"]
                    )
                except Exception as exc:
                    logger.error(
                        "RabbitMQ publishing failed for attendee %s (job %s): %s. Reverting DB state.",
                        attendee_id, new_print_job_id, exc
                    )
                    async with conn.transaction():
                        await conn.execute(
                            "DELETE FROM print_jobs WHERE id = $1;",
                            new_print_job_id
                        )
                        await conn.execute(
                            "UPDATE attendees SET status = 'NOT_REQUESTED', updated_at = NOW() WHERE id = $1;",
                            attendee_id
                        )
                    raise CheckinPublishError("Failed to publish print job to queue") from exc

                return CheckinResponse(
                    attendee_id=updated_row["id"],
                    status="PENDING",
                    print_job_id=str(new_print_job_id)
                )

            # 3. Handle non-claim scenarios
            attendee = await conn.fetchrow(
                "SELECT id, name, status FROM attendees WHERE id = $1;",
                attendee_id
            )

            if not attendee:
                return None

            current_status = attendee["status"]

            if current_status == "PENDING":
                job_id = await conn.fetchval(
                    "SELECT id FROM print_jobs WHERE attendee_id = $1 AND status = 'PENDING' LIMIT 1;",
                    attendee_id
                )
                return CheckinResponse(
                    attendee_id=attendee_id,
                    status="PENDING",
                    print_job_id=str(job_id) if job_id else None,
                    message="Check-in already in progress"
                )
            elif current_status == "CHECKED_IN":
                return CheckinResponse(
                    attendee_id=attendee_id,
                    status="CHECKED_IN",
                    message="Attendee already checked in"
                )
            else:
                return CheckinResponse(
                    attendee_id=attendee_id,
                    status=current_status,
                    message=f"Attendee is in state {current_status}"
                )

    async def process_print_confirmation(self, print_job_id: UUID, result: str) -> Optional[PrintConfirmationResponse]:
        """Process asynchronous print vendor webhook notification idempotently."""
        async with self.db_pool.acquire() as conn:
            # CASE 1: Query print job by print_job_id
            print_job = await conn.fetchrow(
                "SELECT id, attendee_id, status FROM print_jobs WHERE id = $1;",
                print_job_id
            )
            if not print_job:
                logger.warning("Print confirmation received for unknown print_job_id: %s", print_job_id)
                return None

            attendee_id = print_job["attendee_id"]
            attendee = await conn.fetchrow(
                "SELECT id, name, status FROM attendees WHERE id = $1;",
                attendee_id
            )

            if not attendee:
                logger.warning("Print job %s references non-existent attendee %s", print_job_id, attendee_id)
                return None

            current_status = attendee["status"]

            # CASE 2: Known print job + attendee is PENDING
            if current_status == "PENDING":
                async with conn.transaction():
                    await conn.execute(
                        """
                        UPDATE print_jobs
                        SET status = 'COMPLETED',
                            completed_at = NOW()
                        WHERE id = $1;
                        """,
                        print_job_id
                    )
                    await conn.execute(
                        """
                        UPDATE attendees
                        SET status = 'CHECKED_IN',
                            updated_at = NOW()
                        WHERE id = $1
                          AND status = 'PENDING'
                        RETURNING id, name, status;
                        """,
                        attendee_id
                    )

                logger.info("Attendee %s successfully transitioned PENDING -> CHECKED_IN via job %s", attendee_id, print_job_id)
                return PrintConfirmationResponse(
                    status="success",
                    message="Attendee checked in successfully",
                    attendee_id=attendee_id,
                    print_job_id=str(print_job_id)
                )

            # CASE 3: Known print job + attendee already CHECKED_IN (idempotent duplicate webhook)
            elif current_status == "CHECKED_IN":
                logger.info("Duplicate print confirmation received for attendee %s (already CHECKED_IN), job %s", attendee_id, print_job_id)
                return PrintConfirmationResponse(
                    status="success",
                    message="Webhook already processed",
                    attendee_id=attendee_id,
                    print_job_id=str(print_job_id)
                )
            else:
                # Other status handling
                return PrintConfirmationResponse(
                    status="success",
                    message=f"Attendee is in status {current_status}",
                    attendee_id=attendee_id,
                    print_job_id=str(print_job_id)
                )
