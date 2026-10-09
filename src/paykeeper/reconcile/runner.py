"""Ledger-vs-provider reconciliation.

Compares committed `ledger_entries` of type `charge.succeeded` with the
provider's record for the same operation_id. Reports:

- `missing_local`   : provider has it, we don't
- `missing_remote`  : we have it, provider doesn't
- `amount_mismatch` : both sides have it, amounts differ
- `state_mismatch`  : both sides have it, states differ (e.g. refunded
                      remotely but still succeeded locally)

The job is safe to re-run; it never writes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paykeeper.domain.models import Charge


@dataclass(frozen=True, slots=True)
class Mismatch:
    kind: str
    operation_id: str
    local: str
    remote: str


RemoteFetcher = Callable[[timedelta], Awaitable[list[dict[str, Any]]]]


async def reconcile_charges(
    session: AsyncSession,
    *,
    fetch_remote: RemoteFetcher,
    since: timedelta,
    now: datetime | None = None,
) -> list[Mismatch]:
    cutoff = (now or datetime.now(UTC)) - since

    local_rows = (
        (
            await session.execute(
                select(Charge).where(
                    Charge.created_at >= cutoff,
                    Charge.state == "succeeded",
                )
            )
        )
        .scalars()
        .all()
    )
    local_by_op: dict[str, Charge] = {r.operation_id: r for r in local_rows}

    remote_rows = await fetch_remote(since)
    remote_by_op: dict[str, dict[str, Any]] = {
        r["operation_id"]: r for r in remote_rows if r.get("operation_id")
    }

    mismatches: list[Mismatch] = []
    for op, remote in remote_by_op.items():
        local = local_by_op.get(op)
        if local is None:
            remote_descr = (
                f"{remote.get('amount')} {remote.get('currency')} "
                f"({remote.get('provider_ref')})"
            )
            mismatches.append(
                Mismatch(
                    kind="missing_local",
                    operation_id=op,
                    local="(none)",
                    remote=remote_descr,
                )
            )
            continue
        if int(remote["amount"]) != local.amount:
            mismatches.append(
                Mismatch(
                    kind="amount_mismatch",
                    operation_id=op,
                    local=str(local.amount),
                    remote=str(remote["amount"]),
                )
            )
        if (remote.get("status") or "") not in {local.state, ""}:
            mismatches.append(
                Mismatch(
                    kind="state_mismatch",
                    operation_id=op,
                    local=local.state,
                    remote=str(remote.get("status")),
                )
            )

    for op, local in local_by_op.items():
        if op not in remote_by_op:
            mismatches.append(
                Mismatch(
                    kind="missing_remote",
                    operation_id=op,
                    local=f"{local.amount} {local.currency} ({local.provider_ref})",
                    remote="(none)",
                )
            )

    return mismatches


def format_report(mismatches: list[Mismatch]) -> str:
    if not mismatches:
        return "reconcile: no mismatches\n"
    header = f"{'kind':<18} {'operation_id':<40} {'local':<30} {'remote':<30}"
    rows = [header, "-" * len(header)]
    for m in mismatches:
        rows.append(f"{m.kind:<18} {m.operation_id:<40} {m.local:<30} {m.remote:<30}")
    rows.append(f"\n{len(mismatches)} mismatch(es)")
    return "\n".join(rows) + "\n"
