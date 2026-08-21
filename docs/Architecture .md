# Solstice Events Co. — Check-In Kiosk Architecture

### Day 4 Pivot Deliverable

---

## 1. Executive Summary

The original Northstar Retail inventory-sync engagement was discontinued and replaced by a new Solstice Events Co. requirement.

The new system is an event check-in kiosk where successful check-in depends on a badge being physically printed.

The original synchronous design:

```text
QR Scan
   ↓
REST Printer API
   ↓
Wait for success
   ↓
Checked In
```

is no longer viable because the printer vendor is deprecating its synchronous API.

The pivot requires:

```text
QR Scan
   ↓
FastAPI
   ↓
RabbitMQ
   ↓
Print Vendor
   ↓
Webhook
   ↓
FastAPI
   ↓
Checked In
```

The key architectural change is that **request acceptance and print completion are now separate events**.

---

# 2. Scope Delta

This section is important for your Day 5 documentation.

| Original                                | Pivot                          |
| --------------------------------------- | ------------------------------ |
| Northstar Retail                        | Solstice Events Co.            |
| Inventory synchronization               | Event check-in                 |
| Warehouse API                           | Badge-printer vendor           |
| HTTP polling                            | Message queue                  |
| Redis cache                             | PostgreSQL state               |
| Query inventory                         | Submit check-in                |
| Synchronous response                    | Asynchronous webhook           |
| Immediate result after external success | `PENDING` until confirmation   |
| Inventory freshness                     | Print-job state                |
| SKU duplicate concerns                  | Duplicate attendee scan        |
| Warehouse failure boundary              | Queue/webhook failure boundary |

### What was dropped

* Warehouse API
* Inventory polling
* Inventory cache
* Stock query endpoint
* Product/SKU model
* Redis dependency
* Five-minute synchronization

### What was introduced

* Attendee/check-in domain
* Check-in state machine
* RabbitMQ
* Print-job identifier
* Asynchronous print processing
* Webhook confirmation
* Pending UI state
* Idempotent webhook handling
* Duplicate-scan protection

This is where you can explicitly explain the architectural impact of the pivot.

---

# 3. Original Requirement

Keep your existing section, but make it shorter.

```text
QR Scan
   ↓
Synchronous printer REST API
   ↓
Wait for print success
   ↓
Checked In
```

The original requirement required successful physical printing before the attendee could be considered checked in.

---

# 4. Pivot Requirement

```text
QR Scan
   ↓
Create pending check-in
   ↓
Publish print request
   ↓
Return PENDING
        ↓
Printer processes asynchronously
        ↓
Webhook confirmation
        ↓
CHECKED_IN
```

Additional constraints:

* duplicate scans must not create duplicate print jobs
* webhook confirmations may arrive out of order
* duplicate webhook deliveries must be safe
* UI must distinguish `PENDING` from `CHECKED_IN`

---

# 5. Architecture Decisions

Your four decisions are good. I'd keep them.

### Decision 1 — Atomic check-in claim

Use:

```sql
UPDATE attendees
SET status = 'PENDING',
    print_job_id = $2,
    updated_at = NOW()
WHERE id = $1
  AND status = 'NOT_REQUESTED'
RETURNING id;
```

The important principle:

```text
check + transition = one atomic database operation
```

Therefore two simultaneous scans cannot both claim the attendee.

### Decision 2 — `print_job_id`

Generate a UUID for every accepted print request.

It becomes the correlation/idempotency identifier:

```text
attendee
   │
   └── print_job_id
          │
          ├── RabbitMQ message
          │
          └── webhook callback
```

Never rely on callback arrival order.

### Decision 3 — Idempotent webhook

Your three cases are excellent:

```text
Unknown job
    → reject

Known + PENDING
    → CHECKED_IN

Known + CHECKED_IN
    → no-op / success
```

### Decision 4 — State machine

```text
NOT_REQUESTED
      ↓
   PENDING
      ↓
 CHECKED_IN
```

No direct:

```text
NOT_REQUESTED → CHECKED_IN
```

is allowed.

That's an important invariant.

---

# 6. Data Model

I'd actually recommend this:

```sql
CREATE TABLE attendees (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'NOT_REQUESTED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE print_jobs (
    id UUID PRIMARY KEY,
    attendee_id INTEGER NOT NULL REFERENCES attendees(id),
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
```

Why?

Because these are different concepts:

```text
attendees.status
→ current check-in state

print_jobs
→ lifecycle of a specific print attempt
```

It also gives you a much cleaner answer if an instructor asks:

> "What happens if we later add retries?"

You don't need to redesign the attendee table.

For the MVP, you can still enforce **one active print job per attendee**.

---

# 7. Component Architecture

I'd make this diagram your main architecture diagram:

```text
                    ┌──────────────────┐
                    │   Check-in UI    │
                    │ HTML/CSS/TS      │
                    └────────┬─────────┘
                             │
                       POST /checkin
                             │
                             ▼
                    ┌──────────────────┐
                    │     FastAPI      │
                    │  Check-in API    │
                    └────────┬─────────┘
                             │
                    Atomic state change
                             │
                             ▼
                    ┌──────────────────┐
                    │   PostgreSQL     │
                    │ Attendees/Jobs   │
                    └────────┬─────────┘
                             │
                       publish job
                             │
                             ▼
                    ┌──────────────────┐
                    │    RabbitMQ      │
                    │   print_jobs     │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ Simulated Vendor │
                    │    Worker        │
                    └────────┬─────────┘
                             │
                       HTTP webhook
                             │
                             ▼
                    ┌──────────────────┐
                    │     FastAPI      │
                    │ Webhook Handler  │
                    └────────┬─────────┘
                             │
                       update state
                             │
                             ▼
                    ┌──────────────────┐
                    │   PostgreSQL     │
                    └──────────────────┘
```

The frontend can poll:

```text
GET /attendees/{id}
```

to observe:

```text
PENDING → CHECKED_IN
```

---

# 8. API Contracts

Your current contracts are good.

I'd make the semantics explicit:

### `POST /checkin/{attendee_id}`

First scan:

```json
{
  "attendee_id": 3,
  "status": "PENDING",
  "print_job_id": "..."
}
```

Duplicate while pending:

```json
{
  "attendee_id": 3,
  "status": "PENDING",
  "message": "Check-in already in progress"
}
```

Already checked in:

```json
{
  "attendee_id": 3,
  "status": "CHECKED_IN",
  "message": "Attendee already checked in"
}
```

### Webhook

```http
POST /webhook/print-confirmation
```

```json
{
  "print_job_id": "...",
  "result": "success"
}
```

### Status

```http
GET /attendees/{attendee_id}
```

```json
{
  "attendee_id": 3,
  "name": "Jane Doe",
  "status": "PENDING"
}
```

---

# 9. RabbitMQ Design

Keep this section focused.

```text
Exchange/Queue
    ↓
print_jobs
```

Message:

```json
{
  "print_job_id": "...",
  "attendee_id": 3,
  "attendee_name": "Jane Doe"
}
```

Flow:

```text
FastAPI
   ↓
RabbitMQ
   ↓
Simulated Vendor Worker
   ↓
Webhook
```

One thing I'd **not overclaim**: saying "quorum queue" is necessary isn't justified by the brief. If you're using RabbitMQ specifically to demonstrate asynchronous messaging, a durable queue is enough unless the instructors explicitly require RabbitMQ HA semantics.

---

# 10. Failure & Consistency Boundaries

This section is worth adding because it demonstrates senior-level thinking.

### Duplicate scan

```text
NOT_REQUESTED
      ↓
PENDING
```

atomic transition prevents a second job.

### Duplicate webhook

```text
CHECKED_IN
      ↓
same confirmation
      ↓
no-op
```

### Out-of-order webhook

Use:

```text
print_job_id
```

not arrival order.

### Unknown webhook

Reject and log.

### Queue publish failure

This is the **one area I would explicitly document as an implementation decision before coding**.

You currently have:

```text
DB → PENDING
     ↓
RabbitMQ publish
```

What happens if PostgreSQL succeeds but RabbitMQ fails?

You could end up with:

```text
attendee = PENDING
print job = never published
```

For the 48-hour simulation, you can document a simple recovery policy rather than immediately implementing an outbox.

For example:

> If publishing fails after the attendee is marked pending, the API must not report the check-in as successfully queued. The implementation will either roll back the database transaction before commit or explicitly mark the print request as failed, depending on where the queue operation occurs.

**This is something I'd resolve during Slice 2 rather than hide.**

---

# 11. Build Slices

Your existing four slices are exactly the right approach.

I'd make them:

### Slice 1 — Check-in State

```text
PostgreSQL
    ↓
POST /checkin
    ↓
NOT_REQUESTED → PENDING
```

Test concurrent duplicate scans.

Commit:

```text
feat: implement atomic check-in state
```

### Slice 2 — RabbitMQ

```text
POST /checkin
    ↓
PENDING
    ↓
RabbitMQ
```

Test successful and failed publishing.

Commit:

```text
feat: publish badge print jobs
```

### Slice 3 — Vendor + Webhook

```text
RabbitMQ
    ↓
Worker
    ↓
Webhook
    ↓
CHECKED_IN
```

Test:

* valid callback
* duplicate callback
* unknown job
* out-of-order callback

Commit:

```text
feat: handle asynchronous print confirmations
```

### Slice 4 — Kiosk UI + E2E

```text
QR/Attendee
    ↓
PENDING
    ↓
poll status
    ↓
CHECKED_IN
```

Test:

* 3 attendees
* duplicate scan
* only one print job
* confirmation
* duplicate confirmation
* out-of-order confirmation

Commit:

```text
feat: add check-in kiosk interface
```

---

> **Out of scope:** authentication, real printer vendor integration, sophisticated retry/backoff, multi-kiosk coordination, admin functionality, print history/reporting, and production-scale observability.

PostgreSQL itself is **in scope**, because it is central to your correctness guarantees.

---