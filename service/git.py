#!/usr/bin/env python3
"""Repository-required entry point for Git operations."""

import os
import sys


if __name__ == "__main__":
    os.execv("/usr/bin/git", ["git", *sys.argv[1:]])
