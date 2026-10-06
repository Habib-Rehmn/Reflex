"""SQLite metadata ledger, schema version 1. No prompts or response text are accepted."""

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path


class RequestStore:
    def __init__(self, path: str, retention_days: int = 30):
        self.path = Path(path)
        self.retention_days = retention_days

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=0.1)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise sqlite3.DatabaseError("Unsupported database schema version")
            if version == 0:
                connection.execute("""CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                    status_code INTEGER NOT NULL, route TEXT, prediction TEXT,
                    provider_attempted INTEGER NOT NULL, model TEXT,
                    latency_ms REAL, input_tokens INTEGER, output_tokens INTEGER,
                    cached_input_tokens INTEGER, cache_write_input_tokens INTEGER,
                    estimated_cost_usd TEXT, pricing TEXT,
                    error_code TEXT
                )""")
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS requests_created ON requests(created_at)"
                )
                connection.execute("PRAGMA user_version=1")
                connection.commit()
            yield connection
        finally:
            connection.close()

    def prune(self, connection):
        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
        connection.execute("DELETE FROM requests WHERE created_at < ?", (cutoff,))

    def record(self, event: dict):
        with self.connection() as connection, connection:
            self.prune(connection)
            connection.execute(
                """INSERT INTO requests VALUES (
                :request_id, :created_at, :status_code, :route, :prediction,
                :provider_attempted, :model, :latency_ms, :input_tokens, :output_tokens,
                :cached_input_tokens, :cache_write_input_tokens,
                :estimated_cost_usd, :pricing, :error_code
            )""",
                event,
            )

    def recent(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with self.connection() as connection, connection:
            self.prune(connection)
            rows = connection.execute(
                "SELECT * FROM requests ORDER BY created_at DESC, request_id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["provider_attempted"] = bool(item["provider_attempted"])
            for field in ("prediction", "pricing"):
                item[field] = json.loads(item[field]) if item[field] else None
            result.append(item)
        return result

    def metrics(self) -> dict:
        with self.connection() as connection, connection:
            self.prune(connection)
            aggregate = dict(
                connection.execute("""SELECT
                COUNT(*) AS total_requests,
                COALESCE(SUM(status_code >= 400), 0) AS failed_requests,
                COALESCE(SUM(provider_attempted), 0) AS provider_attempts,
                AVG(latency_ms) AS average_latency_ms,
                SUM(input_tokens) AS known_input_tokens,
                SUM(output_tokens) AS known_output_tokens,
                COALESCE(SUM(input_tokens IS NULL OR cached_input_tokens IS NULL), 0)
                    AS unknown_usage_requests,
                COALESCE(SUM(estimated_cost_usd IS NULL), 0) AS unknown_cost_requests
                FROM requests""").fetchone()
            )
            aggregate["routes"] = dict(
                connection.execute(
                    "SELECT COALESCE(route, 'unrouted'), COUNT(*) FROM requests GROUP BY route"
                ).fetchall()
            )
            count = connection.execute("SELECT COUNT(latency_ms) FROM requests").fetchone()[0]
            aggregate["p95_latency_ms"] = (
                connection.execute(
                    "SELECT latency_ms FROM requests WHERE latency_ms IS NOT NULL "
                    "ORDER BY latency_ms LIMIT 1 OFFSET ?",
                    (max(0, math.ceil(count * 0.95) - 1),),
                ).fetchone()[0]
                if count
                else None
            )
            costs = connection.execute(
                "SELECT estimated_cost_usd FROM requests WHERE estimated_cost_usd IS NOT NULL"
            )
            aggregate["known_estimated_cost_usd"] = str(
                sum((Decimal(row[0]) for row in costs), Decimal(0))
            )
            aggregate["retention_days"] = self.retention_days
            return aggregate
