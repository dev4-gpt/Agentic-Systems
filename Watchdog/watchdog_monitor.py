#!/usr/bin/env python3
"""
watchdog_monitor.py
Monitors Hermes gateway and logs, attempts auto-restoration,
and triggers the LangGraph AI diagnostics agent on failures.
"""

import os
import sys
import json
import time
import yaml
import dotenv
import subprocess
from datetime import datetime
from typing import List, Dict, Any, Tuple

# Load environment variables from .env in the Watchdog directory
dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Import the diagnostics agent function
from watchdog_agent import run_diagnostics

# Load configuration
def load_config() -> Dict[str, Any]:
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    if not os.path.exists(config_path):
        return {
            "check_interval_seconds": 30,
            "hermes_logs_dir": "~/.hermes/logs",
            "alerts_dir": "./alerts"
        }
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

config = load_config()
STATE_FILE = os.path.join(os.path.dirname(__file__), "watchdog_state.json")

# Helper to find hermes executable
def find_hermes_executable() -> str:
    # Try finding in PATH
    for path in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(path, "hermes")
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    # Fallback to local default locations
    local_candidates = [
        "/Users/aryamandev/.local/bin/hermes",
        os.path.expanduser("~/.local/bin/hermes"),
        "/usr/local/bin/hermes"
    ]
    for candidate in local_candidates:
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "hermes"  # Fallback to plain string, hope PATH resolves it

HERMES_EXE = find_hermes_executable()

def get_hermes_status() -> Tuple[str, str]:
    """Runs hermes status and parses gateway and cron status."""
    try:
        # Run hermes status
        res = subprocess.run([HERMES_EXE, "status"], capture_output=True, text=True, timeout=10)
        output = res.stdout
        
        # Parse Gateway status
        gateway_status = "Unknown"
        if "Gateway Service" in output:
            lines = output.split("\n")
            for idx, line in enumerate(lines):
                if "Gateway Service" in line:
                    # Look at next lines for Status
                    for next_line in lines[idx+1:idx+5]:
                        if "Status:" in next_line:
                            gateway_status = next_line.replace("Status:", "").strip()
                            break
                    break
        else:
            # Fallback check gateway status
            gateway_res = subprocess.run([HERMES_EXE, "gateway", "status"], capture_output=True, text=True, timeout=5)
            if "not running" in gateway_res.stdout.lower():
                gateway_status = "Stopped"
            elif "running" in gateway_res.stdout.lower():
                gateway_status = "Running"
                
        # Parse Cron status
        cron_status = "Unknown"
        cron_res = subprocess.run([HERMES_EXE, "cron", "status"], capture_output=True, text=True, timeout=5)
        if "not running" in cron_res.stdout.lower():
            cron_status = "Not Running (Gateway Down)"
        elif "active" in cron_res.stdout.lower() or "running" in cron_res.stdout.lower():
            cron_status = "Active"
        else:
            # Parse from stdout
            cron_lines = [l.strip() for l in cron_res.stdout.split("\n") if l.strip()]
            if cron_lines:
                cron_status = cron_lines[0]
                
        return gateway_status, cron_status
    except Exception as e:
        print(f"[Watchdog Monitor] Error getting status: {e}", file=sys.stderr)
        return f"Error ({e})", "Unknown"

def try_auto_restore() -> bool:
    """Attempts to auto-restore the gateway daemon."""
    print("[Watchdog Monitor] Attempting to auto-restore Hermes Gateway...")
    try:
        # Run hermes gateway start
        res = subprocess.run([HERMES_EXE, "gateway", "start"], capture_output=True, text=True, timeout=15)
        print(f"[Watchdog Monitor] Start output: {res.stdout.strip()}")
        if res.stderr:
            print(f"[Watchdog Monitor] Start stderr: {res.stderr.strip()}", file=sys.stderr)
            
        # Re-check status
        time.sleep(2)
        gateway_status, _ = get_hermes_status()
        if "running" in gateway_status.lower() or "active" in gateway_status.lower() or "✓" in gateway_status:
            print("[Watchdog Monitor] Gateway successfully auto-restored!")
            return True
        else:
            print("[Watchdog Monitor] Gateway auto-restoration failed. Status is still down.")
            return False
    except Exception as e:
        print(f"[Watchdog Monitor] Restoration failed with exception: {e}", file=sys.stderr)
        return False

def load_state() -> Dict[str, int]:
    """Loads log offsets from state file."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_state(offsets: Dict[str, int]):
    """Saves log offsets to state file."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(offsets, f)
    except Exception as e:
        print(f"[Watchdog Monitor] Failed to save state: {e}", file=sys.stderr)

def scan_logs(logs_dir: str, offsets: Dict[str, int]) -> Tuple[List[str], Dict[str, int]]:
    """Scans new log lines in agent.log, errors.log, and gateway.log for ERROR or CRITICAL messages."""
    new_errors = []
    updated_offsets = dict(offsets)
    
    logs_dir = os.path.expanduser(logs_dir)
    if not os.path.exists(logs_dir):
        print(f"[Watchdog Monitor] Logs directory '{logs_dir}' does not exist.", file=sys.stderr)
        return new_errors, updated_offsets
        
    log_files = ["agent.log", "errors.log", "gateway.log"]
    
    for filename in log_files:
        filepath = os.path.join(logs_dir, filename)
        if not os.path.exists(filepath):
            continue
            
        # Get file size
        file_size = os.path.getsize(filepath)
        last_offset = offsets.get(filepath, 0)
        
        # If file was rotated or truncated, reset offset
        if file_size < last_offset:
            last_offset = 0
            
        if file_size == last_offset:
            # No new content
            updated_offsets[filepath] = file_size
            continue
            
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                # If first time reading, or offset is 0, let's seek to either end or last 2000 bytes to not spam
                if filepath not in offsets:
                    # First run: read last 2000 bytes to capture recent errors
                    seek_pos = max(0, file_size - 2000)
                    f.seek(seek_pos)
                else:
                    f.seek(last_offset)
                    
                new_content = f.read()
                
                # Check for errors in new content
                lines = new_content.split("\n")
                for line in lines:
                    if not line.strip():
                        continue
                    line_lower = line.lower()
                    if "error" in line_lower or "critical" in line_lower or "exception" in line_lower or "fail" in line_lower:
                        new_errors.append(f"[{filename}] {line}")
                        
            updated_offsets[filepath] = file_size
        except Exception as e:
            print(f"[Watchdog Monitor] Failed to read log file {filepath}: {e}", file=sys.stderr)
            
    return new_errors, updated_offsets

def run_check():
    """Runs a single watchdog check cycle."""
    print(f"\n[Watchdog Monitor] Running check cycle at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...")
    
    # 1. Get Hermes status
    gateway_status, cron_status = get_hermes_status()
    print(f"[Watchdog Monitor] Status: Gateway is [{gateway_status}], Cron is [{cron_status}]")
    
    # 2. Check if Gateway has crashed (expected state is Running/Active)
    is_down = "stop" in gateway_status.lower() or "✗" in gateway_status or "unknown" in gateway_status.lower()
    
    auto_restore_attempted = False
    auto_restore_success = False
    
    if is_down:
        print("[Watchdog Monitor] WARNING: Hermes Gateway appears to be DOWN.")
        auto_restore_attempted = True
        auto_restore_success = try_auto_restore()
        
        # Refresh status after restore attempt
        if auto_restore_success:
            gateway_status, cron_status = get_hermes_status()
            
    # 3. Scan logs for new errors
    offsets = load_state()
    logs_dir = config.get("hermes_logs_dir", "~/.hermes/logs")
    new_errors, updated_offsets = scan_logs(logs_dir, offsets)
    
    if new_errors:
        print(f"[Watchdog Monitor] Detected {len(new_errors)} new error(s) in log files.")
        for err in new_errors[:3]:
            print(f"  -> {err}")
        if len(new_errors) > 3:
            print(f"  -> ... and {len(new_errors) - 3} more.")
            
    # 4. Save updated log offsets
    save_state(updated_offsets)
    
    # 5. Trigger AI diagnostics if gateway is down (and auto-restore failed) or if errors are detected
    trigger_diagnostics = False
    
    # If gateway is still down after auto-restore attempt, or if it went down and we couldn't restore it
    if is_down and not auto_restore_success:
        trigger_diagnostics = True
    # If there are new errors (e.g. cron fails, skill breaks)
    elif new_errors:
        trigger_diagnostics = True
    # If we successfully auto-restored, let's also trigger an alert (warning) so the user knows it crashed but was restored
    elif is_down and auto_restore_success:
        trigger_diagnostics = True
        
    if trigger_diagnostics:
        print("[Watchdog Monitor] Failure state detected. Invoking AI Diagnostics Agent...")
        try:
            run_diagnostics(
                gateway_status=gateway_status,
                cron_status=cron_status,
                new_errors=new_errors,
                auto_restore_attempted=auto_restore_attempted,
                auto_restore_success=auto_restore_success
            )
        except Exception as e:
            print(f"[Watchdog Monitor] AI diagnostics failed: {e}", file=sys.stderr)
    else:
        print("[Watchdog Monitor] System is healthy. No action taken.")

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--loop":
        interval = config.get("check_interval_seconds", 30)
        print(f"[Watchdog Monitor] Starting daemon loop. Checking every {interval} seconds. Press Ctrl+C to exit.")
        try:
            while True:
                run_check()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("[Watchdog Monitor] Watchdog daemon stopped by user.")
    else:
        # Run once
        run_check()

if __name__ == "__main__":
    main()
