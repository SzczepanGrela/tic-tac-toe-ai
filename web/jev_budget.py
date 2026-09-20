"""Durable, conservative accounting shared by rolling replicas on one host.

Every reservation is committed before a provider request is sent. Uncertain
requests stay charged at their upper bound, including after process crashes.
Amounts are integer nano-USD, at the explicitly approved model tariff.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

MODEL = "jev-1.13.0"
NANO_USD_PER_TOKEN = 42
MAX_INPUT_TOKENS = 65536
RESERVATION = MAX_INPUT_TOKENS * NANO_USD_PER_TOKEN
MONTHLY_LIMIT = 1_000_000_000
EVALUATION_LIMIT = 250_000_000
DEFAULT_PATH = "/var/lib/tictactoe/jev/usage.sqlite3"


class LedgerUnavailable(Exception):
    pass


class BudgetExhausted(Exception):
    pass


def utc_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


class BudgetLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).absolute()

    @contextmanager
    def connect(self):
        connection = None
        try:
            # Never silently create a new ledger when a mount or file is lost.
            connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=0.2)
            connection.execute("PRAGMA synchronous=FULL")
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise LedgerUnavailable("Unsupported ledger version")
            with connection:
                yield connection
        except (sqlite3.Error, OSError) as exc:
            raise LedgerUnavailable("Jev accounting is unavailable") from exc
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def initialize(cls, path: str | Path) -> "BudgetLedger":
        path = Path(path)
        # Explicit administrative action, refuses to overwrite any prior data.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(path) as db:
            db.executescript("""
                PRAGMA journal_mode=DELETE;
                PRAGMA synchronous=FULL;
                PRAGMA user_version=1;
                CREATE TABLE policy (
                    id INTEGER PRIMARY KEY CHECK(id=1), model TEXT NOT NULL,
                    price INTEGER NOT NULL, limit_nano INTEGER NOT NULL,
                    paused INTEGER NOT NULL CHECK(paused IN (0,1))
                );
                CREATE TABLE usage (
                    id TEXT PRIMARY KEY, month TEXT NOT NULL,
                    kind TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>=0),
                    state TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX usage_month ON usage(month);
            """)
            # Reconciliation/explicit tariff approval is required before use.
            db.execute("INSERT INTO policy VALUES (1,?,?,?,1)", (MODEL, NANO_USD_PER_TOKEN, MONTHLY_LIMIT))
        return cls(path)

    @staticmethod
    def _policy(db, *, allow_paused=False):
        row = db.execute("SELECT model,price,limit_nano,paused FROM policy WHERE id=1").fetchone()
        if row is None or tuple(row[:3]) != (MODEL, NANO_USD_PER_TOKEN, MONTHLY_LIMIT):
            raise LedgerUnavailable("Jev tariff requires operator review")
        if row[3] and not allow_paused:
            raise LedgerUnavailable("Jev accounting is paused")

    @staticmethod
    def _spent(db, month: str, kind: str | None = None) -> int:
        sql = "SELECT COALESCE(SUM(amount),0) FROM usage WHERE month=?"
        parameters = [month]
        if kind is not None:
            sql += " AND kind=?"
            parameters.append(kind)
        return db.execute(sql, parameters).fetchone()[0]

    def status(self) -> dict:
        with self.connect() as db:
            self._policy(db)
            month = utc_month()
            used = self._spent(db, month)
            return {"month": month, "used_nano_usd": used, "limit_nano_usd": MONTHLY_LIMIT,
                    "available": used + RESERVATION <= MONTHLY_LIMIT}

    def reserve(self, kind: str = "production") -> str:
        if kind not in ("production", "evaluation"):
            raise ValueError("Invalid usage kind")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._policy(db)
            month = utc_month()
            if self._spent(db, month) + RESERVATION > MONTHLY_LIMIT:
                raise BudgetExhausted("Monthly Jev budget exhausted")
            if kind == "evaluation" and self._spent(db, month, kind) + RESERVATION > EVALUATION_LIMIT:
                raise BudgetExhausted("Evaluation budget exhausted")
            reservation_id = uuid.uuid4().hex
            db.execute("INSERT INTO usage(id,month,kind,amount,state) VALUES (?,?,?,?,?)",
                       (reservation_id, month, kind, RESERVATION, "reserved"))
        return reservation_id

    def settle(self, reservation_id: str, input_tokens: int) -> None:
        if type(input_tokens) is not int or not 0 <= input_tokens <= MAX_INPUT_TOKENS:
            raise LedgerUnavailable("Invalid provider usage; reservation retained")
        cost = input_tokens * NANO_USD_PER_TOKEN
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT amount,state FROM usage WHERE id=?", (reservation_id,)).fetchone()
            if row is None:
                raise LedgerUnavailable("Unknown reservation")
            if row[1] == "settled":
                if row[0] != cost:
                    raise LedgerUnavailable("Inconsistent settlement")
                return
            if row != (RESERVATION, "reserved"):
                raise LedgerUnavailable("Invalid reservation")
            db.execute("UPDATE usage SET amount=?,state='settled' WHERE id=?", (cost, reservation_id))

    def pause(self) -> None:
        with self.connect() as db:
            db.execute("UPDATE policy SET paused=1 WHERE id=1")

    def reconcile(self, spent_nano: int) -> None:
        """Operator confirms actual spending; reconciliation never reduces it."""
        if type(spent_nano) is not int or spent_nano < 0:
            raise ValueError("Invalid spending total")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._policy(db, allow_paused=True)
            month = utc_month()
            difference = max(0, spent_nano - self._spent(db, month))
            if difference:
                db.execute("INSERT INTO usage(id,month,kind,amount,state) VALUES (?,?,?,?,?)",
                           (uuid.uuid4().hex, month, "adjustment", difference, "settled"))
            db.execute("UPDATE policy SET paused=0 WHERE id=1")

    def backup(self, destination: str | Path) -> None:
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with self.connect() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
                # A restored copy must not spend before reconciliation.
                with target:
                    target.execute("UPDATE policy SET paused=1 WHERE id=1")
            finally:
                target.close()
