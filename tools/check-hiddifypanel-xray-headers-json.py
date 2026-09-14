#!/usr/bin/env python3
"""Classify the Hiddify xray.py headers serialization implementation."""

from __future__ import annotations

import argparse
from pathlib import Path


BUGGY = "q[k] = v"
FIXED = "q[k] = json.dumps(v) if isinstance(v, (dict, list)) else v"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("xray_py", type=Path, nargs="?")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        assert classify(FIXED) == ("FIX_PRESENT", 0)
        assert classify(
            "proxy.get('transport') not in {ProxyTransport.xhttp}\n" + BUGGY
        ) == ("BUG_PRESENT", 1)
        assert classify("unrelated") == ("UNKNOWN_LAYOUT", 2)
        print("SELF_TEST_OK")
        return 0
    if args.xray_py is None:
        parser.error("xray_py is required unless --self-test is used")

    text = args.xray_py.read_text(encoding="utf-8")
    message, exit_code = classify(text)
    print(message)
    return exit_code


def classify(text: str) -> tuple[str, int]:
    if FIXED in text:
        return "FIX_PRESENT", 0
    if BUGGY in text and "proxy.get('transport') not in {ProxyTransport.xhttp}" in text:
        return "BUG_PRESENT", 1
    return "UNKNOWN_LAYOUT", 2


if __name__ == "__main__":
    raise SystemExit(main())
