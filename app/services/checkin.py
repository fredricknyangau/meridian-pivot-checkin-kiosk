import uuid
from typing import List, Optional
import asyncpg
from app.schemas.checkin import (
    AttendeeSchema,
    CheckinResponse,
    AttendeeDetailResponse,
)


class CheckinService:
    def __init__(self, db_pool: asyncpg.Pool):
        self.db_pool = db_pool

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
                    # Successfully claimed - create corresponding print_jobs record
                    await conn.execute(
                        """
                        INSERT INTO print_jobs (id, attendee_id, status)
                        VALUES ($1, $2, 'PENDING');
                        """,
                        new_print_job_id,
                        attendee_id
                    )

            # Transaction committed cleanly here!
            if updated_row:
                return CheckinResponse(
                    attendee_id=updated_row["id"],
                    status="PENDING",
                    print_job_id=str(new_print_job_id)
                )

            # 2. Atomic update returned no row - handle non-claim scenarios
            attendee = await conn.fetchrow(
                "SELECT id, name, status FROM attendees WHERE id = $1;",
                attendee_id
            )

            if not attendee:
                return None

            current_status = attendee["status"]

            if current_status == "PENDING":
                # Find existing PENDING print job
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
