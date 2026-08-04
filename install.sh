#!/usr/bin/env bash
cd "$(dirname "$0")"
command -v python3 >/dev/null 2>&1 || { echo "Install Python 3 first (python.org), then rerun."; exit 1; }
python3 autopilot.py
