#!/usr/bin/env python3
"""Convenience entry point: delegates to battle.integration.mount_display_app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battle.integration.mount_display_app import main

if __name__ == "__main__":
    sys.exit(main())
