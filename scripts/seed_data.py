import asyncio
import os
import asyncpg
from app.config import settings


async def run_migration_and_seed():
    print(f"Connecting to database: {settings.DATABASE_URL}")
    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        # 1. Read and execute migration script
        migration_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "app",
            "database",
            "migrations",
            "001_create_checkin_tables.sql"
        )
        with open(migration_path, "r", encoding="utf-8") as f:
            migration_sql = f.read()

        print("Executing migration 001_create_checkin_tables.sql...")
        await conn.execute(migration_sql)
        print("Migration executed successfully.")

        # 2. Seed 3 attendees: Attendee One, Attendee Two, Attendee Three
        print("Seeding attendees...")
        attendees = ["Attendee One", "Attendee Two", "Attendee Three"]
        for name in attendees:
            existing = await conn.fetchrow("SELECT id FROM attendees WHERE name = $1;", name)
            if not existing:
                await conn.execute(
                    "INSERT INTO attendees (name, status) VALUES ($1, 'NOT_REQUESTED');",
                    name
                )
                print(f"Seeded attendee: {name}")
            else:
                await conn.execute(
                    "UPDATE attendees SET status = 'NOT_REQUESTED' WHERE id = $1;",
                    existing["id"]
                )
                print(f"Updated attendee {name} status to NOT_REQUESTED")

        count = await conn.fetchval("SELECT COUNT(*) FROM attendees;")
        print(f"Total attendees in DB: {count}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_migration_and_seed())
