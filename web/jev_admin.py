"""Offline administration; never prints or reads the provider key."""
from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation

from web.jev_budget import BudgetLedger, DEFAULT_PATH, LedgerUnavailable


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_PATH)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    sub.add_parser("pause")
    backup = sub.add_parser("backup")
    backup.add_argument("destination")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--spent-usd", required=True)
    reconcile.add_argument("--confirm-tariff", choices=["jev-1.13.0:0.042/M"], required=True)
    args = parser.parse_args()
    try:
        ledger = BudgetLedger(args.db)
        if args.action == "init":
            BudgetLedger.initialize(args.db)
            print("Ledger initialized, paused until tariff and spending are confirmed.")
        elif args.action == "status":
            print(json.dumps(ledger.status()))
        elif args.action == "pause":
            ledger.pause()
            print("Jev accounting paused.")
        elif args.action == "backup":
            ledger.backup(args.destination)
            print("Backup created; reconciliation is required after restoring it.")
        else:
            amount = Decimal(args.spent_usd)
            if not amount.is_finite() or amount < 0:
                raise ValueError("Invalid spending total")
            ledger.reconcile(int((amount * 1_000_000_000).to_integral_value(rounding="ROUND_CEILING")))
            print("Spending reconciled; ledger resumed.")
    except (LedgerUnavailable, OSError, ValueError, InvalidOperation) as exc:
        parser.exit(1, f"Accounting error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
