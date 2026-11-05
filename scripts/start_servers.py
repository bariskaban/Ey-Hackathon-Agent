"""Start every MCP server that is built, over HTTP, for the MCP Inspector and manual testing.

    python scripts/start_servers.py

Each server listens on its own port at http://127.0.0.1:<port>/mcp. Press Ctrl+C to stop all.
The agent itself does not need this: the gateway starts the servers over stdio on its own.

To inspect one server:
    npx @modelcontextprotocol/inspector
    (transport: Streamable HTTP, URL: http://127.0.0.1:8102/mcp)
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from servers.registry import SERVERS


def main() -> int:
    processes: list[tuple[str, subprocess.Popen]] = []
    for name, info in SERVERS.items():
        if not info.is_built():
            print(f"  - {name}: not built yet, skipped")
            continue
        process = subprocess.Popen(
            [sys.executable, "-m", info.module, "--transport", "streamable-http", "--port", str(info.port)],
            cwd=ROOT,
        )
        processes.append((name, process))
        print(f"  + {name}: http://127.0.0.1:{info.port}/mcp")
    if not processes:
        print("no servers are built yet")
        return 1
    print("running; press Ctrl+C to stop")
    try:
        while True:
            for name, process in processes:
                if process.poll() is not None:
                    print(f"{name} exited with code {process.returncode}")
                    return process.returncode or 1
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for _name, process in processes:
            process.terminate()
        for _name, process in processes:
            process.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
