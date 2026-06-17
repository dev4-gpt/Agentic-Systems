#!/bin/bash
# run_watchdog.sh
# Runs the Watchdog monitor using the project's virtual environment.

# Get the directory where this script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
WORKSPACE_DIR="$(dirname "$DIR")"

# Path to python in virtual environment
PYTHON_BIN="$WORKSPACE_DIR/.venv/bin/python"

if [ ! -f "$PYTHON_BIN" ]; then
    echo "Error: Virtual environment python not found at $PYTHON_BIN"
    echo "Please make sure the virtual environment .venv exists in the project root."
    exit 1
fi

echo "Activating virtual environment and starting Gateway Watchdog..."
cd "$DIR"
exec "$PYTHON_BIN" watchdog_monitor.py "$@"
