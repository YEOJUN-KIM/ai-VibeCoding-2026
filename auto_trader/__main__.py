"""`python -m auto_trader` 실행 진입점."""

import uvicorn

from .settings import settings


def main() -> None:
    uvicorn.run(
        "auto_trader.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_reload,
    )


if __name__ == "__main__":
    main()
