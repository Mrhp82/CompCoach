"""PostgreSQL persistence for the existing CompCoach domain operations.

The server connects directly to a private schema, never through the public
Supabase Data API. This deliberately reuses the SQLite implementation's domain
methods so mobile interactions, audit IDs and optimistic versions have the
same behavior in both deployments. The small SQL adapter below only translates
the SQLite features actually used by that implementation.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from psycopg.pq import TransactionStatus
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool, PoolTimeout
except ImportError:  # SQLite installations can still run before installing cloud deps.
    psycopg = None  # type: ignore[assignment]

try:
    from .storage import CompCoachDB, CompCoachError
except ImportError:  # ``streamlit run app.py`` from the application directory.
    from storage import CompCoachDB, CompCoachError


class PostgresConfigurationError(CompCoachError):
    """A cloud configuration is invalid; callers must not fall back to SQLite."""


def _validated_connection_info(database_url: str) -> str:
    if psycopg is None:
        raise PostgresConfigurationError(
            "PostgreSQL support requires psycopg. Install the app requirements."
        )
    if not str(database_url or "").strip():
        raise PostgresConfigurationError("The PostgreSQL connection URL is missing.")
    try:
        options = conninfo_to_dict(str(database_url).strip())
    except Exception:
        # libpq's original error can contain a URL and its password.
        raise PostgresConfigurationError("The PostgreSQL connection URL is invalid.") from None
    hosts = options.get("host", "").split(",")
    host_addresses = options.get("hostaddr", "").split(",") if options.get("hostaddr") else []
    local = bool(hosts) and all(host in {"localhost", "127.0.0.1", "::1"} for host in hosts)
    local = local and all(address in {"127.0.0.1", "::1"} for address in host_addresses)
    if not options.get("host"):
        raise PostgresConfigurationError("Specify a PostgreSQL host explicitly.")
    if "6543" in options.get("port", "").split(","):
        raise PostgresConfigurationError(
            "Use the Supabase Session Pooler on port 5432, not the Transaction Pooler."
        )
    if not local and options.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
        raise PostgresConfigurationError(
            "Remote PostgreSQL connections require sslmode=require or certificate verification."
        )
    # Keep connection/lock failures bounded during a competition. Do not accept
    # libpq startup options that could override the private search_path.
    options.pop("options", None)
    options.setdefault("connect_timeout", "12")
    return make_conninfo(**options)


def _bind_placeholders(sql: str) -> str:
    """Convert qmark binds while retaining question marks inside SQL literals."""

    result: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(sql):
        char = sql[index]
        if quote:
            result.append(char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    result.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
            result.append(char)
        elif char == "?":
            result.append("%s")
        else:
            result.append(char)
        index += 1
    return "".join(result)


def _translate_sql(sql: str, *, bind: bool = True) -> tuple[str, bool]:
    """Translate the fixed SQL dialect used by CompCoachDB, keeping data bound."""

    translated = sql.strip().rstrip(";")
    ignore = bool(re.match(r"INSERT\s+OR\s+IGNORE\b", translated, re.I))
    translated = re.sub(r"INSERT\s+OR\s+IGNORE\b", "INSERT", translated, flags=re.I)
    translated = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGSERIAL PRIMARY KEY",
        translated,
        flags=re.I,
    )
    translated = re.sub(
        r"\b([A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?)\s+COLLATE\s+NOCASE\b",
        r"lower(\1)",
        translated,
        flags=re.I,
    )
    translated = re.sub(r"\bCASE\s+WHEN\s+\?\s+THEN\b", "CASE WHEN ? <> 0 THEN", translated, flags=re.I)
    # PostgreSQL does not allow a SELECT alias inside an ORDER BY expression.
    # The directory uses this expression to move used coaches below unused ones.
    if re.search(r"ORDER\s+BY\s+assignment_count\s*>\s*0", translated, re.I):
        inner, ordering = re.split(r"\bORDER\s+BY\b", translated, maxsplit=1, flags=re.I)
        ordering = re.sub(r"\bcoaches\.name\b", "name", ordering)
        translated = f"SELECT * FROM ({inner}) AS compcoach_ordered ORDER BY {ordering}"
    if ignore:
        translated += " ON CONFLICT DO NOTHING"
    returns_id = bool(re.match(r"INSERT\s+INTO\s+(?:actions|coach_assignment_history)\s*\(", translated, re.I))
    if returns_id and not re.search(r"\bRETURNING\b", translated, re.I):
        translated += " RETURNING id"
    if bind:
        translated = _bind_placeholders(translated.replace("%", "%%"))
    return translated, returns_id


def _rollback_or_close(raw: Any) -> None:
    """Preserve the original error if its session has already disconnected."""
    if raw.closed:
        return
    try:
        raw.rollback()
    except psycopg.Error:
        # A failed rollback cannot leave a session available to another coach.
        raw.close()


class _Cursor:
    def __init__(self, cursor: Any = None, *, rows: list[dict[str, Any]] | None = None,
                 lastrowid: int | None = None) -> None:
        self.cursor = cursor
        self.rows = rows
        self.lastrowid = lastrowid
        self.rowcount = cursor.rowcount if cursor is not None else len(rows or [])

    def fetchone(self) -> dict[str, Any] | None:
        if self.rows is not None:
            return self.rows.pop(0) if self.rows else None
        return self.cursor.fetchone()

    def fetchall(self) -> list[dict[str, Any]]:
        if self.rows is not None:
            rows, self.rows = self.rows, []
            return rows
        return self.cursor.fetchall()

    def __iter__(self):
        return iter(self.fetchall())


class _Connection:
    """SQLite-shaped facade around a private-schema psycopg connection."""

    def __init__(self, raw: Any, schema: str, lock_key: int) -> None:
        self.raw = raw
        self.schema = schema
        self.lock_key = lock_key
        self._writer_lock_acquired = False

    def _begin_write(self) -> None:
        if self.raw.info.transaction_status == TransactionStatus.IDLE:
            self.raw.execute("BEGIN")
            self._writer_lock_acquired = False
        if not self._writer_lock_acquired:
            self.raw.execute("SELECT pg_advisory_xact_lock(%s)", (self.lock_key,))
            self._writer_lock_acquired = True

    def execute(self, sql: str, parameters: Iterable[Any] | None = None) -> _Cursor:
        normalized = sql.strip().rstrip(";")
        if re.fullmatch(r"BEGIN(?:\s+IMMEDIATE)?", normalized, re.I):
            self._begin_write()
            return _Cursor(rows=[])
        pragma = re.fullmatch(r"PRAGMA\s+table_info\(([A-Za-z_][A-Za-z_0-9]*)\)", normalized, re.I)
        if pragma:
            cursor = self.raw.execute(
                "SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                (self.schema, pragma.group(1)),
            )
            return _Cursor(cursor)
        if normalized.upper().startswith("PRAGMA "):
            return _Cursor(rows=[])
        if normalized.upper() == "COMMIT":
            self.commit()
            return _Cursor(rows=[])
        if normalized.upper() == "ROLLBACK":
            self.rollback()
            return _Cursor(rows=[])
        parameters_tuple = tuple(parameters) if parameters is not None else None
        translated, returns_id = _translate_sql(normalized, bind=parameters_tuple is not None)
        # Standalone writes are committed on successful context exit. Once the
        # lock is taken, subsequent reads are serialized with other writers.
        if re.match(r"(?:INSERT|UPDATE|DELETE|ALTER|CREATE|DROP)\b", normalized, re.I):
            self._begin_write()
        try:
            cursor = self.raw.execute(translated, parameters_tuple, prepare=False)
        except psycopg.IntegrityError as exc:
            # The shared domain currently catches SQLite's class for duplicate
            # coach names. Preserve that contract without leaking SQL/parameters.
            raise sqlite3.IntegrityError("A PostgreSQL constraint prevented this update.") from None
        if returns_id:
            row = cursor.fetchone()
            return _Cursor(cursor, rows=[row] if row else [], lastrowid=int(row["id"]) if row else None)
        return _Cursor(cursor)

    def executescript(self, script: str) -> None:
        # CompCoach's bootstrap script contains DDL only, without procedural
        # dollar-quoted blocks. The canonical Postgres script is executed raw.
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    def executemany(self, sql: str, parameters: Iterable[Iterable[Any]]) -> _Cursor:
        # Keep interval closure, directory rename and role updates in the same
        # advisory-locked transaction as their surrounding domain operation.
        self._begin_write()
        count = 0
        for values in parameters:
            count += max(self.execute(sql, values).rowcount, 0)
        cursor = _Cursor(rows=[])
        cursor.rowcount = count
        return cursor

    def commit(self) -> None:
        self.raw.commit()
        self._writer_lock_acquired = False

    def rollback(self) -> None:
        _rollback_or_close(self.raw)
        self._writer_lock_acquired = False


class PostgresCompCoachDB(CompCoachDB):
    """Persistent PostgreSQL/Supabase implementation of CompCoachDB."""

    backend = "postgres"

    def __init__(self, database_url: str, *, schema: str = "compcoach", initialize: bool = True,
                 pool_max_size: int = 4) -> None:
        if not re.fullmatch(r"compcoach(?:_[a-z0-9_]+)?", str(schema)):
            raise PostgresConfigurationError("Use the private compcoach schema.")
        self._conninfo = _validated_connection_info(database_url)
        self.schema = schema
        self.path = None  # No local persistence and no SQLite fallback.
        self._lock_key = int.from_bytes(
            hashlib.blake2b(f"compcoach-writer:{schema}".encode(), digest_size=8).digest(),
            "big", signed=True,
        )
        if not isinstance(pool_max_size, int) or isinstance(pool_max_size, bool) or not 1 <= pool_max_size <= 8:
            raise PostgresConfigurationError("The cloud connection pool size must be between 1 and 8.")
        self._pool = ConnectionPool(
            self._conninfo, min_size=1, max_size=pool_max_size, timeout=12,
            max_idle=60, max_lifetime=1800, open=False,
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
            configure=self._configure_connection,
        )
        self._pool_started = False
        self._pool_start_lock = threading.Lock()
        try:
            if initialize:
                self._initialize()
        except BaseException:
            self._pool.close()
            raise

    def _configure_connection(self, raw: Any) -> None:
        # Configure each physical connection once; mobile board refreshes reuse
        # TLS sessions rather than opening a session for every small query.
        raw.execute(f'SET search_path TO "{self.schema}", pg_catalog')
        raw.execute("SET lock_timeout TO '12s'")
        raw.execute("SET statement_timeout TO '30s'")

    def close(self) -> None:
        """Release database sessions for CLI completion and test teardown."""
        self._pool.close()

    def _ensure_pool_open(self) -> None:
        # wait() is a startup check, not a checkout check. Running it for every
        # borrow can wait for an idle connection while other coaches hold all
        # available sessions, defeating the pool's normal queue behavior.
        if not self._pool_started:
            with self._pool_start_lock:
                if not self._pool_started:
                    self._pool.open(wait=True, timeout=12)
                    self._pool_started = True

    @contextmanager
    def _connection(self):
        # Checkouts are short and independent; physical sessions are bounded
        # and reused across board updates. Session Pooler supports IPv4.
        try:
            self._ensure_pool_open()
            raw = self._pool.getconn(timeout=12)
        except (psycopg.Error, PoolTimeout):
            raise CompCoachError(
                "The cloud database is unavailable. Check the connection settings or retry."
            ) from None
        try:
            facade = _Connection(raw, self.schema, self._lock_key)
            try:
                yield facade
            except psycopg.Error:
                _rollback_or_close(raw)
                raise CompCoachError(
                    "The cloud database could not complete this operation. Retry or check its configuration."
                ) from None
            except BaseException:
                _rollback_or_close(raw)
                raise
            else:
                raw.commit()
        except psycopg.Error:
            _rollback_or_close(raw)
            raise CompCoachError(
                "The cloud database could not initialize this connection. Check its configuration."
            ) from None
        finally:
            # Psycopg_pool discards broken sessions and replaces them. A
            # rollback returns healthy sessions without an open transaction.
            _rollback_or_close(raw)
            self._pool.putconn(raw)

    def initialize_schema(self, conn: _Connection) -> None:
        """Install additive DDL in the caller's transaction, without backfills.

        The migration CLI uses this after its target-empty guard, making the
        guard, schema creation and row import one atomic operation.
        """
        script = Path(__file__).with_name("postgres_schema.sql").read_text(encoding="utf-8")
        # The schema name is validated above; all data and credentials stay out
        # of this DDL interpolation. A test can use an isolated compcoach_* schema.
        if self.schema != "compcoach":
            script = re.sub(r"\bcompcoach\b", self.schema, script)
        conn.execute("BEGIN IMMEDIATE")
        conn.raw.execute(script, prepare=False)

    def _initialize(self) -> None:
        with self._connection() as conn:
            self.initialize_schema(conn)
            conn.commit()
        # Reuse additive/backfill migrations too. This is important after a
        # legacy SQLite database is imported with the original IDs preserved.
        super()._initialize()


CompCoachPostgresDB = PostgresCompCoachDB
