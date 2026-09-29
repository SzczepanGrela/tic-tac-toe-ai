from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import os
import sqlite3
import subprocess
import sys

import pytest

from web import jev_budget
from web.jev_budget import BudgetExhausted, BudgetLedger, LedgerUnavailable, MONTHLY_LIMIT, RESERVATION


@pytest.fixture
def ledger(tmp_path):
    result = BudgetLedger.initialize(tmp_path / "usage.sqlite3")
    result.reconcile(0)
    return result


def reserve_in_process(path):
    try:
        return BudgetLedger(path).reserve()
    except BudgetExhausted:
        return None


def test_processes_share_budget_atomically(ledger):
    ledger.reconcile(MONTHLY_LIMIT - RESERVATION)
    with ProcessPoolExecutor(2, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(reserve_in_process, [str(ledger.path)] * 4))
    assert sum(item is not None for item in results) == 1
    assert ledger.status()["used_nano_usd"] == MONTHLY_LIMIT
    assert not ledger.status()["available"]


def test_settlement_is_idempotent_and_never_refunds_uncertain_work(ledger):
    known, unknown = ledger.reserve(), ledger.reserve()
    ledger.settle(known, 100)
    ledger.settle(known, 100)
    assert ledger.status()["used_nano_usd"] == RESERVATION + 4200
    with pytest.raises(LedgerUnavailable):
        ledger.settle(known, 50)
    with pytest.raises(LedgerUnavailable):
        ledger.settle(unknown, -1)


def test_reservation_survives_process_crash(ledger):
    command = "from web.jev_budget import BudgetLedger; import os,sys; BudgetLedger(sys.argv[1]).reserve(); os._exit(17)"
    result = subprocess.run([sys.executable, "-c", command, str(ledger.path)], check=False)
    assert result.returncode == 17
    assert BudgetLedger(ledger.path).status()["used_nano_usd"] == RESERVATION


def test_missing_corrupt_and_paused_ledgers_fail_closed(tmp_path):
    path = tmp_path / "usage.sqlite3"
    with pytest.raises(LedgerUnavailable):
        BudgetLedger(path).reserve()
    assert not path.exists()
    path.write_text("not sqlite")
    with pytest.raises(LedgerUnavailable):
        BudgetLedger(path).reserve()
    path.unlink()
    ledger = BudgetLedger.initialize(path)
    with pytest.raises(LedgerUnavailable):
        ledger.reserve()
    with pytest.raises(FileExistsError):
        BudgetLedger.initialize(path)
    assert path.stat().st_mode & 0o777 == 0o600


def test_month_rollover_does_not_change_existing_reservations(ledger, monkeypatch):
    monkeypatch.setattr(jev_budget, "utc_month", lambda: "2026-09")
    reserved = ledger.reserve()
    monkeypatch.setattr(jev_budget, "utc_month", lambda: "2026-10")
    ledger.settle(reserved, 100)
    assert ledger.status()["used_nano_usd"] == 0
    monkeypatch.setattr(jev_budget, "utc_month", lambda: "2026-09")
    assert ledger.status()["used_nano_usd"] == 4200


def test_backup_requires_reconciliation_and_does_not_reset_usage(ledger, tmp_path):
    ledger.reserve()
    copy = tmp_path / "backup.sqlite3"
    ledger.backup(copy)
    restored = BudgetLedger(copy)
    with pytest.raises(LedgerUnavailable):
        restored.reserve()
    restored.reconcile(0)
    assert restored.status()["used_nano_usd"] == RESERVATION
    assert ledger.status()["used_nano_usd"] == RESERVATION
    assert copy.stat().st_mode & 0o777 == 0o600


def test_evaluation_has_a_sub_limit_in_the_same_monthly_budget(ledger):
    for _ in range(jev_budget.EVALUATION_LIMIT // RESERVATION):
        ledger.reserve("evaluation")
    with pytest.raises(BudgetExhausted):
        ledger.reserve("evaluation")
    ledger.reserve()
    assert ledger.status()["used_nano_usd"] > jev_budget.EVALUATION_LIMIT


def test_evaluation_limit_increase_reuses_existing_usage(ledger, monkeypatch):
    previous_limit = 250_000_000
    with monkeypatch.context() as previous_release:
        previous_release.setattr(jev_budget, "EVALUATION_LIMIT", previous_limit)
        for _ in range(previous_limit // RESERVATION):
            ledger.reserve("evaluation")
        with pytest.raises(BudgetExhausted, match="Evaluation budget exhausted"):
            ledger.reserve("evaluation")
        used_before = ledger.status()["used_nano_usd"]

    replacement = BudgetLedger(ledger.path)
    assert replacement.status()["used_nano_usd"] == used_before
    replacement.reserve("evaluation")
    assert replacement.status()["used_nano_usd"] == used_before + RESERVATION


def test_evaluation_cannot_exceed_remaining_shared_budget(ledger):
    ledger.reserve("production")
    ledger.reconcile(MONTHLY_LIMIT - RESERVATION)
    ledger.reserve("evaluation")
    with pytest.raises(BudgetExhausted, match="Monthly Jev budget exhausted"):
        ledger.reserve("evaluation")
    assert ledger.status()["used_nano_usd"] == MONTHLY_LIMIT
    assert not ledger.status()["available"]


def test_changed_tariff_is_not_accepted(ledger):
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE policy SET price=1")
    with pytest.raises(LedgerUnavailable):
        ledger.reserve()
