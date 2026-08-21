CREATE TABLE IF NOT EXISTS attendees (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'NOT_REQUESTED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS print_jobs (
    id UUID PRIMARY KEY,
    attendee_id INTEGER NOT NULL REFERENCES attendees(id),
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL
);

-- Constraint: an attendee cannot have more than one active/pending print job
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_print_job_per_attendee
ON print_jobs (attendee_id)
WHERE status = 'PENDING';
