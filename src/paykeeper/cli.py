from __future__ import annotations

import asyncio
from datetime import timedelta

import typer
import uvicorn

from paykeeper.config import get_settings
from paykeeper.db.session import create_engine, create_sessionmaker

app = typer.Typer(help="paykeeper administrative commands")


@app.command()
def serve(
    host: str = "0.0.0.0",
    port: int | None = None,
    reload: bool = False,
) -> None:
    settings = get_settings()
    uvicorn.run(
        "paykeeper.api.app:create_app",
        factory=True,
        host=host,
        port=port or settings.http_port,
        reload=reload,
    )


@app.command("outbox-drain")
def outbox_drain(batch_size: int = 100) -> None:
    """Publish pending outbox events to stdout, exclusive via advisory lock."""

    async def _run() -> int:
        from paykeeper.outbox.drain import drain_once

        settings = get_settings()
        engine = create_engine(settings.database_url)
        sm = create_sessionmaker(engine)
        try:
            async with sm() as session:
                return await drain_once(session, batch_size=batch_size)
        finally:
            await engine.dispose()

    published = asyncio.run(_run())
    typer.echo(f"published={published}", err=True)


def _parse_duration(s: str) -> timedelta:
    s = s.strip().lower()
    if s.endswith("h"):
        return timedelta(hours=int(s[:-1]))
    if s.endswith("m"):
        return timedelta(minutes=int(s[:-1]))
    if s.endswith("s"):
        return timedelta(seconds=int(s[:-1]))
    if s.endswith("d"):
        return timedelta(days=int(s[:-1]))
    return timedelta(seconds=int(s))


@app.command()
def reconcile(since: str = "24h") -> None:
    """Compare the ledger against provider state; non-zero exit on mismatch."""

    async def _run() -> int:
        from paykeeper.providers.fake import FakeProvider
        from paykeeper.reconcile.runner import format_report, reconcile_charges

        settings = get_settings()
        engine = create_engine(settings.database_url)
        sm = create_sessionmaker(engine)
        provider = FakeProvider(
            secret=settings.webhook_secret.get_secret_value(), instance="primary"
        )

        async def fetch_remote(_window: timedelta) -> list[dict[str, object]]:
            return [
                {
                    "operation_id": c.operation_id,
                    "provider_ref": c.provider_ref,
                    "amount": c.amount,
                    "currency": c.currency,
                    "status": c.status,
                }
                for c in provider.all_charges()
            ]

        try:
            async with sm() as session:
                mismatches = await reconcile_charges(
                    session,
                    fetch_remote=fetch_remote,
                    since=_parse_duration(since),
                )
            typer.echo(format_report(mismatches), nl=False)
            return 0 if not mismatches else 1
        finally:
            await engine.dispose()

    exit_code = asyncio.run(_run())
    raise typer.Exit(code=exit_code)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
