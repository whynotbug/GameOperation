from __future__ import annotations

import argparse
from pathlib import Path

from game_test_py.tools.recorder import ActionRecorder, ActionReplayer


def main() -> None:
    parser = argparse.ArgumentParser(description="Record and replay keyboard/mouse actions")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rec = sub.add_parser("record", help="Record actions until ESC is released")
    p_rec.add_argument("output", type=Path, help="Path to save the recording JSON")

    p_rep = sub.add_parser("replay", help="Replay actions from a JSON file")
    p_rep.add_argument("input", type=Path, help="Path of the recording JSON to replay")

    args = parser.parse_args()

    if args.cmd == "record":
        print("Recording... Press and release ESC to stop.")
        ActionRecorder().record(args.output)
        print(f"Saved recording to {args.output}")
        return

    if args.cmd == "replay":
        print(f"Replaying {args.input}...")
        ActionReplayer().replay(args.input)
        print("Done.")


if __name__ == "__main__":
    main()



