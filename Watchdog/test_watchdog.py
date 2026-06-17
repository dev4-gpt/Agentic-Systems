#!/usr/bin/env python3
"""
test_watchdog.py
Simulates a gateway crash and error log event, runs the watchdog check,
and prints the resulting AI-generated markdown alert.
"""

import os
import sys
import shutil
import tempfile
import yaml
from unittest.mock import patch

# Ensure the parent directory is in the python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import watchdog_monitor
import watchdog_agent

def run_simulation():
    print("=" * 60)
    print("      HERMES GATEWAY WATCHDOG SIMULATION RUNNER")
    print("=" * 60)
    
    # 1. Create a temporary directory for simulated logs and alerts
    temp_dir = tempfile.mkdtemp()
    sim_logs_dir = os.path.join(temp_dir, "logs")
    sim_alerts_dir = os.path.join(temp_dir, "alerts")
    os.makedirs(sim_logs_dir, exist_ok=True)
    os.makedirs(sim_alerts_dir, exist_ok=True)
    
    print(f"[Sim] Created temp directory for simulation: {temp_dir}")
    
    # 2. Write simulated log errors into simulated agent.log and errors.log
    sim_agent_log = os.path.join(sim_logs_dir, "agent.log")
    sim_errors_log = os.path.join(sim_logs_dir, "errors.log")
    
    mock_errors = [
        "2026-05-23 15:30:11,402 INFO run_agent: Executing scheduled cron job: weekly_data_sync",
        "2026-05-23 15:30:12,198 ERROR tools: Failed executing skill 'apple-notes' - Exception: SecurityError: Keychain access denied. The user must approve application access.",
        "2026-05-23 15:30:12,204 CRITICAL cron: Cron job 'weekly_data_sync' exited with status 1. Traceback: File '/Users/aryamandev/.hermes/skills/apple-notes.py', line 45, in run_sync"
    ]
    
    with open(sim_agent_log, "w") as f:
        f.write("\n".join(mock_errors) + "\n")
        
    with open(sim_errors_log, "w") as f:
        f.write("2026-05-23 15:30:12,204 CRITICAL cron: Cron job 'weekly_data_sync' exited with status 1.\n")
        
    print("[Sim] Injected mock errors into temporary logs.")

    # 3. Modify watchdog configuration dynamically for this run
    original_config = dict(watchdog_monitor.config)
    
    # Configure watchdog_monitor and watchdog_agent to use the temp folders
    watchdog_monitor.config["hermes_logs_dir"] = sim_logs_dir
    watchdog_monitor.config["alerts_dir"] = sim_alerts_dir
    
    watchdog_agent.config["alerts_dir"] = sim_alerts_dir
    
    # 4. Mock the status commands to simulate a CRASHED gateway
    # get_hermes_status will return ("Stopped", "Not Running")
    # try_auto_restore will return False (representing a failed restart)
    
    print("[Sim] Mocking gateway status: Gateway is STOPPED, Cron is DOWN.")
    
    with patch('watchdog_monitor.get_hermes_status') as mock_status, \
         patch('watchdog_monitor.try_auto_restore') as mock_restore:
         
        mock_status.return_value = ("Stopped", "Not Running (Gateway Down)")
        mock_restore.return_value = False  # Auto-restoration failed
        
        # Run a single watchdog check cycle
        print("[Sim] Starting watchdog monitor check cycle...")
        watchdog_monitor.run_check()
        
    # 5. Check if any markdown alerts were generated in the simulated alerts directory
    alert_files = os.listdir(sim_alerts_dir)
    print(f"\n[Sim] Checked alerts directory. Found alert files: {alert_files}")
    
    if alert_files:
        alert_file_path = os.path.join(sim_alerts_dir, alert_files[0])
        print(f"\n[Sim] Successfully generated AI Alert: {alert_files[0]}\n")
        print("=" * 60)
        print("                 AI-GENERATED ALERT MARKDOWN")
        print("=" * 60)
        with open(alert_file_path, "r") as f:
            print(f.read())
        print("=" * 60)
        
        # Copy the generated alert file to our local alerts directory in the workspace for user visibility
        local_alerts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alerts")
        os.makedirs(local_alerts_dir, exist_ok=True)
        shutil.copy(alert_file_path, os.path.join(local_alerts_dir, alert_files[0]))
        print(f"[Sim] Copied generated alert to workspace folder: Watchdog/alerts/{alert_files[0]}")
    else:
        print("[Sim] ERROR: No alert files were generated in the alerts directory.", file=sys.stderr)
        
    # Clean up temp directories
    shutil.rmtree(temp_dir)
    print(f"[Sim] Cleaned up temporary directory: {temp_dir}")
    print("[Sim] Simulation complete.")
    print("=" * 60)

if __name__ == "__main__":
    run_simulation()
