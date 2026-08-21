# Build Prompts — Solstice Events Check-In Kiosk

Paste each prompt into Claude Code, Cursor, or Antigravity **in order**.

**Rule:** Do not move to the next prompt until the current slice has been implemented, run, tested, and committed. Each slice should produce evidence for the Day 5 evaluation.

## Target Architecture

```text
HTML / CSS / TypeScript
          |
          v
       FastAPI
       /      \
      v        v
PostgreSQL   RabbitMQ
                |
                v
        Simulated Printer
                |
             webhook
                |
                v
             FastAPI
                |
                v
           PostgreSQL
```

Core state:

```text
NOT_REQUESTED → PENDING → CHECKED_IN
```

Core correctness guarantees:

- Two concurrent scans cannot create two print jobs for the same attendee.
- `print_job_id` uniquely identifies a specific print request.
- Webhook processing does not depend on arrival order.
- Duplicate webhook delivery is safe.
- The UI shows `PENDING` until backend confirmation changes the state to `CHECKED_IN`.
- PostgreSQL is the source of truth for check-in state.
- RabbitMQ carries asynchronous print requests.

---

# Prompt 1 — Project Scaffold, PostgreSQL Schema, and Seed Data

```text
Scaffold a minimal FastAPI check-in kiosk service for Solstice Events Co.

Use:
- Python 3.12
- FastAPI
- PostgreSQL
- asyncpg
- Pydantic
- python-dotenv
- RabbitMQ with pika
- pytest for testing

Use raw SQL with asyncpg. Do NOT use SQLAlchemy or another ORM.

Create this initial structure:

app/
├── main.py
├── config.py
├── database/
│   ├── connection.py
│   └── migrations/
│       └── 001_create_checkin_tables.sql
├── routes/
│   ├── checkin.py
│   └── webhook.py
├── services/
│   └── checkin.py
├── messaging/
│   └── rabbitmq.py
└── schemas/
    └── checkin.py

tests/
.env.example
requirements.txt
README.md

Create an `attendees` table:

- id SERIAL PRIMARY KEY
- name TEXT NOT NULL
- status TEXT NOT NULL DEFAULT 'NOT_REQUESTED'
- created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
- updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()

Create a `print_jobs` table:

- id UUID PRIMARY KEY
- attendee_id INTEGER NOT NULL REFERENCES attendees(id)
- status TEXT NOT NULL DEFAULT 'PENDING'
- created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
- completed_at TIMESTAMPTZ NULL

The print job ID must be unique because it identifies one specific print request.

Add a database constraint so an attendee cannot have more than one active/pending print job. Use a PostgreSQL partial unique index if appropriate.

Seed exactly 3 attendees:

1. Attendee One
2. Attendee Two
3. Attendee Three

All must start as NOT_REQUESTED.

Configure an asyncpg connection pool using FastAPI application startup/shutdown lifecycle.

Add environment configuration for:
- DATABASE_URL
- RABBITMQ_URL
- RABBITMQ_QUEUE
- API_BASE_URL

Do not implement check-in, RabbitMQ publishing, webhook processing, or frontend logic yet.

Keep the project minimal and runnable.

After implementation:
1. Run the migration.
2. Run the seed.
3. Start FastAPI.
4. Verify all 3 attendees exist and have NOT_REQUESTED status.
5. Add a basic pytest test proving the application imports and the schema assumptions are correct.
6. Do not continue until everything passes.
```

**Verify:**

```text
3 attendees exist
3 × NOT_REQUESTED
FastAPI starts
Database connection works
Tests pass
```

Suggested commit:

```text
chore: scaffold check-in kiosk service
```

---

# Prompt 2 — Atomic Check-In Endpoint

```text
Implement the first business slice: atomic attendee check-in.

Create:

POST /checkin/{attendee_id}

and:

GET /attendees/{attendee_id}

Keep all business logic inside services/checkin.py. Routes should remain thin.

For POST /checkin/{attendee_id}:

1. Generate a UUID for a new print_job_id.
2. Atomically claim the attendee with PostgreSQL.

Use this pattern:

UPDATE attendees
SET status = 'PENDING',
    updated_at = NOW()
WHERE id = $1
  AND status = 'NOT_REQUESTED'
RETURNING id, name, status;

Do not use SELECT-then-UPDATE for this transition.

3. Only after the attendee is successfully claimed, create the corresponding print_jobs record using the generated print_job_id.
4. Use a database transaction so the attendee transition and print-job creation either both succeed or both roll back.
5. If the atomic update returns no row:
   - Check whether the attendee exists.
   - If it does not exist, return 404.
   - If it exists and is PENDING, return the existing PENDING state with an appropriate duplicate/in-progress message.
   - If it exists and is CHECKED_IN, return the existing CHECKED_IN state with an appropriate already-checked-in message.
6. Do not generate or persist a new print job for duplicate scans.
7. Do not publish to RabbitMQ yet.

Response for a successful first scan:

{
  "attendee_id": 1,
  "status": "PENDING",
  "print_job_id": "..."
}

Response for an attendee already pending:

{
  "attendee_id": 1,
  "status": "PENDING",
  "message": "Check-in already in progress"
}

Response for an attendee already checked in:

{
  "attendee_id": 1,
  "status": "CHECKED_IN",
  "message": "Attendee already checked in"
}

GET /attendees/{attendee_id} should return:

{
  "attendee_id": 1,
  "name": "Attendee One",
  "status": "PENDING"
}

Add unit tests for:
- first check-in
- duplicate pending scan
- already checked-in attendee
- unknown attendee
- print_job_id remains unchanged after duplicate scan

Add a concurrency-oriented test or test helper demonstrating that two attempts cannot both transition the same attendee from NOT_REQUESTED to PENDING.

Do not implement RabbitMQ yet.
```

**Verify:**

```text
First scan → PENDING + print_job_id
Second scan → PENDING, same print_job_id
No second print_jobs row
Unknown attendee → 404
Tests pass
```

Suggested commit:

```text
feat: implement atomic attendee check-in
```

---

# Prompt 3 — RabbitMQ Print Job Publishing

```text
Add RabbitMQ publishing to the successful check-in flow.

Use pika.

Create a queue named:

print_jobs

Declare it as:
- durable=True
- quorum queue using {"x-queue-type": "quorum"}

Messages must use persistent delivery mode.

Message payload:

{
  "print_job_id": "...",
  "attendee_id": 1,
  "attendee_name": "Attendee One"
}

Only the first successful NOT_REQUESTED → PENDING transition may publish a message.

Duplicate scans must never publish another message.

Important reliability requirement:

The database and RabbitMQ are separate systems and do not share one transaction.

Do not pretend they are transactionally atomic.

Implement the simplest safe behavior appropriate for this 48-hour exercise:

1. Complete the PostgreSQL state transition and print-job creation.
2. Attempt RabbitMQ publication.
3. If publication fails, handle the failure explicitly rather than silently leaving the system inconsistent.
4. Do not return a successful queued response if the message was definitely not published.
5. Log the failure without exposing internal RabbitMQ details to API clients.

Do not introduce a full distributed transaction or transactional-outbox implementation unless absolutely necessary.

Do not perform blocking pika operations directly on the FastAPI event loop. Encapsulate RabbitMQ access in messaging/rabbitmq.py and ensure the request handler does not block the async event loop unnecessarily.

Add tests for:
- successful publish
- duplicate scan does not publish
- RabbitMQ publish failure is handled
- message contains the correct print_job_id and attendee information

Make the publisher connection lifecycle clean and configurable through environment variables.

Do not build the simulated printer yet.
```

**Verify:**

```text
First scan → exactly 1 RabbitMQ message
Duplicate scan → 0 additional messages
Message contains correct print_job_id
Publish failure is controlled
Tests pass
```

Suggested commit:

```text
feat: publish badge print jobs to rabbitmq
```

---

# Prompt 4 — Simulated Printer Worker + Idempotent Webhook

```text
Implement the asynchronous completion side.

Build:

1. simulated_printer.py
2. POST /webhook/print-confirmation

The simulated printer must:

- Connect to RabbitMQ.
- Consume from print_jobs.
- Use manual acknowledgments.
- Parse the print_job_id, attendee_id, and attendee_name.
- Simulate a short printing delay.
- Call the FastAPI webhook using HTTP.
- Send:

{
  "print_job_id": "...",
  "result": "success"
}

Only acknowledge the RabbitMQ message after the simulated vendor request has been handled successfully.

The webhook endpoint must be:

POST /webhook/print-confirmation

The webhook service must identify the print request using print_job_id, never by arrival order.

Handle exactly these cases:

CASE 1 — Unknown print_job_id

- No matching print job exists.
- Log the event.
- Return HTTP 404.
- Do not modify any attendee state.

CASE 2 — Known print job + attendee is PENDING

- Mark the print job completed.
- Transition attendee from PENDING to CHECKED_IN.
- Set completed_at.
- Return success.

The state transition must be conditional:

UPDATE attendees
SET status = 'CHECKED_IN',
    updated_at = NOW()
WHERE id = $1
  AND status = 'PENDING'
RETURNING id, name, status;

CASE 3 — Known print job + attendee already CHECKED_IN

- Treat it as a duplicate webhook.
- Do not error.
- Do not create another print job.
- Do not change the attendee state.
- Return success.

The webhook must therefore be idempotent.

Also ensure print_job_id is unique at the database level.

Add tests for:
- valid webhook
- unknown print_job_id
- duplicate webhook
- PENDING → CHECKED_IN transition
- CHECKED_IN remains CHECKED_IN after duplicate confirmation

Do not assume webhook confirmations arrive in the same order as check-in requests.
```

**Verify:**

```text
RabbitMQ message
    ↓
Printer worker
    ↓
Webhook
    ↓
PENDING → CHECKED_IN

Second webhook
    ↓
200/success
    ↓
No state corruption
```

Suggested commit:

```text
feat: handle asynchronous print confirmations
```

---

# Prompt 5 — Full End-to-End Correctness Test

```text
Create a pytest-based end-to-end test for the complete Solstice Events check-in flow.

The test must exercise the real application, PostgreSQL, RabbitMQ, and simulated printer/webhook behavior where practical.

Use the 3 seeded attendees.

Scenario:

1. Check in attendee 1.
   Expected: PENDING.

2. Immediately check in attendee 1 again.
   Expected:
   - PENDING
   - same print_job_id
   - no second print_jobs record
   - no second RabbitMQ print request

3. Check in attendee 2.
   Expected: PENDING.

4. Check in attendee 3.
   Expected: PENDING.

5. Capture all three print_job_ids.

6. Deliver webhook confirmations in a different order:
   - attendee 3
   - attendee 1
   - attendee 2

7. Verify each attendee becomes CHECKED_IN based on its own print_job_id.

8. Deliver attendee 1's webhook a second time.
   Expected:
   - successful/idempotent response
   - attendee remains CHECKED_IN
   - no new print job

9. Verify final database state:
   - all 3 attendees = CHECKED_IN
   - exactly 3 print jobs exist
   - exactly 3 distinct print_job_ids exist

10. Print a clear PASS/FAIL summary.

The test must explicitly demonstrate that callback ordering does not determine which attendee is updated. The print_job_id must be the correlation mechanism.

Do not weaken the test by mocking away the database or RabbitMQ for the end-to-end scenario.

Keep unit tests separate for isolated business logic.
```

**Verify and save the output.**

Expected final evidence:

```text
PASS — first check-in
PASS — duplicate scan prevented
PASS — no second print job
PASS — attendee 2 check-in
PASS — attendee 3 check-in
PASS — out-of-order confirmations
PASS — duplicate webhook idempotency
PASS — final state: 3 CHECKED_IN
PASS — exactly 3 print jobs
```

Suggested commit:

```text
test: verify end-to-end asynchronous check-in flow
```

---

# Prompt 6 — Minimal HTML/CSS/TypeScript Kiosk UI

```text
Build a minimal kiosk frontend using plain HTML, CSS, and TypeScript.

Do NOT use React, Vue, Angular, Tailwind, or another frontend framework.

Create:

frontend/
├── index.html
├── styles.css
├── src/
│   └── main.ts
├── tsconfig.json
└── package.json

The frontend should provide a simple demonstration kiosk for the 3 seeded attendees.

Requirements:

1. Display the 3 attendees.
2. Provide a "Check In" action for each attendee.
3. Call:

POST /checkin/{attendee_id}

4. After the POST succeeds, display PENDING immediately.

IMPORTANT:
Do not display CHECKED IN merely because POST /checkin succeeds.

The backend remains the source of truth.

5. While status is PENDING, poll:

GET /attendees/{attendee_id}

every 1–2 seconds.

6. When the backend returns CHECKED_IN:
   - stop polling
   - update the UI to CHECKED IN
   - show a clear success state

7. If the attendee is already PENDING:
   display:
   "Check-in already in progress"

8. If the attendee is already CHECKED_IN:
   display:
   "Already checked in"

9. Show a loading state while submitting.
10. Show a controlled error message if the API is unavailable.
11. Keep the design intentionally minimal and kiosk-friendly.
12. Do not put check-in business rules inside TypeScript.
13. TypeScript should only handle presentation and API interaction.
14. Compile TypeScript to JavaScript.
15. Configure FastAPI to serve the compiled frontend, or provide a simple documented local development setup.

Add a small status indicator for:

NOT_REQUESTED
PENDING
CHECKED_IN

The UI should make the asynchronous nature of the system obvious.

Do not add authentication, admin dashboards, user management, or unnecessary frontend features.

After implementation, manually demonstrate:

- attendee 1 → PENDING → CHECKED IN
- attendee 1 scanned again → already checked in
- attendee 2 → PENDING → CHECKED IN
- attendee 3 → PENDING → CHECKED IN
```

**Verify:**

```text
UI
 ↓
POST /checkin
 ↓
PENDING
 ↓
printer worker
 ↓
webhook
 ↓
CHECKED_IN
 ↓
UI updates
```

Suggested commit:

```text
feat: add check-in kiosk frontend
```

---

# Prompt 7 — Final Hardening, Documentation, and Evidence

```text
Perform a final review of the Solstice Events check-in kiosk.

Do not introduce new features.

Review the implementation against these requirements:

1. Atomic duplicate-scan protection.
2. NOT_REQUESTED → PENDING → CHECKED_IN state model.
3. Unique print_job_id correlation.
4. RabbitMQ asynchronous print requests.
5. Simulated printer consumer.
6. Webhook confirmation.
7. Idempotent duplicate webhook handling.
8. Out-of-order confirmation handling.
9. PostgreSQL as the source of truth.
10. UI remains PENDING until backend confirmation.
11. Three-attendee scenario.
12. One duplicate scan.
13. No duplicate print job.
14. Tests pass.

Check for:
- blocking operations inside async FastAPI handlers
- leaked database connections
- leaked RabbitMQ connections
- raw infrastructure errors returned to clients
- unsafe SQL construction
- duplicated business logic
- routes containing business logic
- missing validation
- incorrect transaction boundaries
- incorrect webhook state transitions
- incorrect status codes
- unnecessary abstractions

Run the complete test suite.

Then update:

README.md
docs/ARCHITECTURE.md
docs/SCOPE_DELTA.md
docs/TEST_EVIDENCE.md

SCOPE_DELTA.md must document:

- original Northstar requirement
- what was dropped
- new Solstice requirement
- what was introduced
- architectural consequences of the pivot
- which Days 1–2 RabbitMQ concepts transferred into the new implementation

TEST_EVIDENCE.md must include:

- commands used to run tests
- final test result
- the 3-attendee scenario
- duplicate scan result
- number of print jobs created
- out-of-order webhook result
- duplicate webhook result

ARCHITECTURE.md must explain:

- source of truth
- state model
- atomic state transition
- print_job_id correlation
- RabbitMQ boundary
- webhook boundary
- failure boundaries
- why the architecture is intentionally minimal

Do not claim production guarantees that the implementation does not actually provide.

Finish by reporting:
- tests passed
- known limitations
- final project structure
- recommended final Git commit
```

Suggested commit:

```text
docs: finalize architecture and pivot evidence
```

---

# Final Git History

Aim for a history roughly like:

```text
chore: scaffold check-in kiosk service
feat: implement atomic attendee check-in
feat: publish badge print jobs to rabbitmq
feat: handle asynchronous print confirmations
test: verify end-to-end asynchronous check-in flow
feat: add check-in kiosk frontend
docs: finalize architecture and pivot evidence
```

This tells a very clear story:

```text
Scaffold
   ↓
State correctness
   ↓
Asynchronous messaging
   ↓
Webhook completion
   ↓
End-to-end verification
   ↓
UI
   ↓
Evidence/documentation
```

## Important implementation rule

Do **not** let the coding agent "improve" this into a large production platform.

For this evaluation, the strongest implementation is the smallest one that can convincingly demonstrate:

```text
              PIVOT
                ↓
     synchronous → asynchronous
                ↓
           RabbitMQ
                ↓
       webhook confirmation
                ↓
        explicit state model
                ↓
       atomic duplicate guard
                ↓
        idempotent processing
                ↓
          tested end-to-end
```

That is the architectural story the implementation needs to prove.
