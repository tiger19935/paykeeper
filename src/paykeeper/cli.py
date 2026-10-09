from __future__ import annotations

import typer
import uvicorn

from paykeeper.config import get_settings

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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
