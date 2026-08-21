from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class AttendeeSchema(BaseModel):
    id: int
    name: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PrintJobSchema(BaseModel):
    id: UUID
    attendee_id: int
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CheckinResponse(BaseModel):
    attendee_id: int
    status: str
    print_job_id: Optional[str] = None
    message: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AttendeeDetailResponse(BaseModel):
    attendee_id: int
    name: str
    status: str

    model_config = ConfigDict(from_attributes=True)
