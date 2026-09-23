"""`python -m quiz_sift.server [--root DIR] [--port N]` でローカル API を起動する。"""

import argparse
from pathlib import Path

from quiz_sift.server.handler import DEFAULT_PORT, HOST, make_server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="quiz_sift.server", description="quiz-sift のローカル HTTP API")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="data/ と out/ を持つ作業ルート")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    with make_server(args.root, args.port) as server:
        print(f"quiz-sift API: http://{HOST}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
