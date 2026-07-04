#!/usr/bin/env python3
"""
Nightly consolidation script for Obsidian Brain.
Rebuilds the FAISS index to incorporate new/changed notes.

Usage: python consolidate.py [--force]
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure OBSIDIAN_VAULT_PATH is set
vault_path = os.environ.get("OBSIDIAN_VAULT_PATH")
if not vault_path:
    print("ERROR: OBSIDIAN_VAULT_PATH not set")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brain import consolidate

def main():
    force = "--force" in sys.argv
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] Starting consolidation (force={force})...")

    result = consolidate(force=force)

    print(f"[{now}] Result: {json.dumps(result)}")

    if result.get("status") == "built":
        print(f"  Notes: {result.get('notes')}")
        print(f"  Chunks: {result.get('chunks')}")
    elif result.get("status") == "already_current":
        print("  Index already up to date")

    print(f"[{now}] Done.")

if __name__ == "__main__":
    main()
