from __future__ import annotations

import asyncio

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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
