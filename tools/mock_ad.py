"""Mock Active Directory — SQLite-backed fake student directory.

Encodes SJSU's real rules so the demo is believable:
- 21-minute lockout cooldown (pulled from SJSU IT docs)
- Alumni cutoff: no reset if last_semester > 24 months ago
- Recovery email required for reset

Run `python -m tools.mock_ad --seed` once to create and populate the DB.
"""
import argparse
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import CONFIG


SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    student_id       TEXT PRIMARY KEY,
    first_name       TEXT NOT NULL,
    last_name        TEXT NOT NULL,
    recovery_email   TEXT NOT NULL,
    account_active   INTEGER NOT NULL DEFAULT 1,
    locked_until     TEXT,                -- ISO timestamp, NULL if not locked
    last_reset_at    TEXT,
    last_semester_end TEXT NOT NULL       -- ISO date, for alumni cutoff
);
"""

SEED_STUDENTS = [
    # student_id, first, last, email, active, locked_until, last_reset, last_semester
    ("012345678", "Maya",   "Patel",    "maya.p@gmail.com",    1, None, None, "2026-05-15"),
    ("123456789", "Diego",  "Ramirez",  "diego.r@outlook.com", 1,
        (datetime.now(timezone.utc) + timedelta(minutes=14)).isoformat(), None, "2026-05-15"),
    ("234567890", "Jordan", "Kim",      "jkim@yahoo.com",      1, None, None, "2026-05-15"),
    ("345678901", "Priya",  "Shah",     "priya.s@icloud.com",  0, None, None, "2022-12-20"),  # alumni, inactive
    ("456789012", "Alex",   "Chen",     "achen@gmail.com",     1, None, None, "2026-05-15"),
]


def _conn() -> sqlite3.Connection:
    Path(CONFIG.students_db_path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(CONFIG.students_db_path)
    c.row_factory = sqlite3.Row
    return c


def seed() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)
        c.execute("DELETE FROM students")
        c.executemany(
            "INSERT INTO students VALUES (?,?,?,?,?,?,?,?)", SEED_STUDENTS
        )
    print(f"Seeded {len(SEED_STUDENTS)} students → {CONFIG.students_db_path}")


# ---------- tool functions used by agents ----------------------------------

def lookup_student(student_id: str) -> dict | None:
    """Return student row as dict, or None if not found / inactive."""
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM students WHERE student_id = ? AND account_active = 1",
            (student_id,),
        ).fetchone()
    return dict(row) if row else None


def check_cooldown(student_id: str) -> dict:
    """Return {in_cooldown, minutes_remaining, cooldown_until}."""
    s = lookup_student(student_id)
    if not s:
        return {"success": False, "detail": "Student not found", "in_cooldown": False}
    if not s["locked_until"]:
        return {"success": True, "in_cooldown": False, "minutes_remaining": 0}

    until = datetime.fromisoformat(s["locked_until"])
    now = datetime.now(timezone.utc)
    if until <= now:
        return {"success": True, "in_cooldown": False, "minutes_remaining": 0}

    remaining = int((until - now).total_seconds() // 60) + 1
    return {
        "success": True,
        "in_cooldown": True,
        "minutes_remaining": remaining,
        "cooldown_until": s["locked_until"],
    }


def reset_password(student_id: str) -> dict:
    """Reset and send a temp link. Enforces alumni cutoff."""
    s = lookup_student(student_id)
    if not s:
        return {"success": False, "detail": "Student not found or inactive."}

    # Alumni cutoff: 24 months after last semester end
    last_sem = datetime.fromisoformat(s["last_semester_end"])
    if (datetime.now(timezone.utc) - last_sem.replace(tzinfo=timezone.utc)).days > 730:
        return {
            "success": False,
            "detail": "Alumni cutoff exceeded (>24 months since last enrollment). Escalate to iSupport.",
        }

    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "UPDATE students SET last_reset_at = ?, locked_until = NULL WHERE student_id = ?",
            (now, student_id),
        )
    return {
        "success": True,
        "detail": f"Temporary reset link sent to {s['recovery_email']}. Expires in 1 hour.",
        "recovery_email_masked": s["recovery_email"][:2] + "***" + s["recovery_email"][s["recovery_email"].index("@"):],
    }


def unlock_account(student_id: str) -> dict:
    """Unlock an account — fails if still in cooldown."""
    cooldown = check_cooldown(student_id)
    if cooldown.get("in_cooldown"):
        return {
            "success": False,
            "detail": f"Account still in cooldown — {cooldown['minutes_remaining']} minutes remaining.",
            "cooldown_until": cooldown["cooldown_until"],
        }
    with _conn() as c:
        c.execute("UPDATE students SET locked_until = NULL WHERE student_id = ?", (student_id,))
    return {"success": True, "detail": "Account unlocked."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true", help="(Re)create + populate the mock DB")
    args = parser.parse_args()
    if args.seed:
        seed()