import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(database_path: Path) -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    with connect(database_path) as connection:
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        count = connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
        if count == 0:
            connection.execute(
                "INSERT INTO schema_version(version, applied_at_utc) VALUES (?, ?)",
                (1, datetime.now(UTC).isoformat()),
            )
        current = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        if current < 2:
            connection.execute(
                "INSERT INTO schema_version(version, applied_at_utc) VALUES (?, ?)",
                (2, datetime.now(UTC).isoformat()),
            )
