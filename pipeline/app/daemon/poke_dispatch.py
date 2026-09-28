#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, socket, sys
from pathlib import Path
HOME = Path.home()
DEFAULT_SOCKET = Path(os.environ.get("AUTOSCRIBE_DISPATCH_SOCKET", str(HOME / ".local/run/autoscribe/dispatch.sock")))
def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--repo", required=True); ap.add_argument("--commit", required=True); ap.add_argument("--ref"); ap.add_argument("--socket", default=str(DEFAULT_SOCKET)); args = ap.parse_args()
    payload = {"repo": str(Path(args.repo).resolve()), "commit": args.commit, "ref": args.ref}
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try: s.sendto(json.dumps(payload, separators=(",", ":")).encode("utf-8"), args.socket)
    except OSError as exc:
        print(f"autoscribe dispatch poke missed: {exc}", file=sys.stderr); return 0
    finally: s.close()
    return 0
if __name__ == "__main__": raise SystemExit(main())
