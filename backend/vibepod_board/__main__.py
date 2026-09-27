"""`python -m vibepod_board`: serve the board with uvicorn on HOST:PORT (default 0.0.0.0:3000)."""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "vibepod_board.main:create_app",
        factory=True,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "3000")),
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
