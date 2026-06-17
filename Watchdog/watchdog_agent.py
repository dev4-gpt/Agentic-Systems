#!/usr/bin/env python3
"""
watchdog_agent.py
LangChain and LangGraph AI Diagnostics Agent for Hermes Gateway.
"""

import os
import sys
import yaml
import dotenv
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Any, TypedDict

# Load environment variables from .env in the Watchdog directory
dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Import LangChain & LangGraph components
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, START, END

# Define Agent State
class AgentState(TypedDict):
    gateway_status: str
    cron_status: str
    new_errors: List[str]
    auto_restore_attempted: bool
    auto_restore_success: bool
    diagnosis: str
    action_plan: str
    email_subject: str
    email_body: str
    notification_sent: bool

# Load Configuration
def load_config() -> Dict[str, Any]:
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    if not os.path.exists(config_path):
        # Fallback default config
        return {
            "ollama": {"base_url": "http://localhost:11434", "model": "qwen3.5:4b"},
            "alerts_dir": "./alerts",
            "email": {"smtp_enabled": False}
        }
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

config = load_config()

# Initialize Local LLM via ChatOllama
llm = ChatOllama(
    base_url=config["ollama"]["base_url"],
    model=config["ollama"]["model"],
    temperature=0.2
)

# -------------------------------------------------------------
# LangGraph Node Implementations
# -------------------------------------------------------------

def diagnose_failure(state: AgentState) -> Dict[str, Any]:
    """Node: Analyze status and error logs to diagnose the issue."""
    errors_text = "\n".join(state["new_errors"]) if state["new_errors"] else "No recent log errors detected."
    restore_str = "Auto-restore ATTEMPTED" if state["auto_restore_attempted"] else "Auto-restore NOT attempted"
    restore_status = "SUCCESSFUL" if state["auto_restore_success"] else "FAILED or not applicable"
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are a Senior Site Reliability Engineer (SRE) and AI Agent debugger. "
            "Your task is to diagnose failures in the Hermes Agent Gateway system.\n\n"
            "Analyze the provided gateway status, cron job status, and recent error logs. "
            "Identify what broke, which component failed, and what the root cause is. "
            "Be specific. If there are no errors but the gateway crashed, explain that."
        )),
        ("user", (
            "SYSTEM DIAGNOSTICS:\n"
            "- Gateway Status: {gateway_status}\n"
            "- Cron Status: {cron_status}\n"
            "- Auto-Restore: {restore_str} (Status: {restore_status})\n\n"
            "RECENT ERROR LOGS:\n"
            "{errors_text}\n\n"
            "Provide a concise but detailed diagnosis of the failure."
        ))
    ])
    
    chain = prompt | llm
    response = chain.invoke({
        "gateway_status": state["gateway_status"],
        "cron_status": state["cron_status"],
        "restore_str": restore_str,
        "restore_status": restore_status,
        "errors_text": errors_text
    })
    
    return {"diagnosis": response.content}


def recommend_action(state: AgentState) -> Dict[str, Any]:
    """Node: Propose a clear, actionable resolution plan based on the diagnosis."""
    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are an SRE expert. Based on the diagnosis of a Hermes Gateway system failure, "
            "propose a step-by-step actionable recommendation plan for the administrator to resolve the issue. "
            "Ensure the steps are practical (e.g. check API keys, run hermes doctor, restart service, edit configuration, debug python files)."
        )),
        ("user", (
            "DIAGNOSIS:\n{diagnosis}\n\n"
            "Provide the step-by-step actionable recommendations."
        ))
    ])
    
    chain = prompt | llm
    response = chain.invoke({"diagnosis": state["diagnosis"]})
    return {"action_plan": response.content}


def draft_email_alert(state: AgentState) -> Dict[str, Any]:
    """Node: Generate subject line and draft the markdown email body."""
    errors_summary = "Gateway Crash / Error Log detected"
    if not state["auto_restore_success"] and state["auto_restore_attempted"]:
        errors_summary = "Gateway CRITICAL - Restore Failed"
    elif state["auto_restore_success"]:
        errors_summary = "Gateway Warning - Auto-Restored"
        
    subject = f"[WATCHDOG ALERT] Hermes Gateway: {errors_summary}"
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are a communication bot. Write a professional, highly readable markdown alert/email. "
            "It will notify human-in-the-loop administrators about a system event/failure and actions taken.\n"
            "Incorporate the diagnosis and recommended action plan cleanly. "
            "Use clear headings, markdown lists, and highlights."
        )),
        ("user", (
            "DIAGNOSTIC SUMMARY:\n"
            "- Gateway Status: {gateway_status}\n"
            "- Cron Status: {cron_status}\n"
            "- Auto-Restore Attempted: {restore_attempted}\n"
            "- Auto-Restore Success: {restore_success}\n\n"
            "DIAGNOSIS:\n{diagnosis}\n\n"
            "ACTION PLAN:\n{action_plan}\n\n"
            "Draft the markdown body of the notification email."
        ))
    ])
    
    chain = prompt | llm
    response = chain.invoke({
        "gateway_status": state["gateway_status"],
        "cron_status": state["cron_status"],
        "restore_attempted": str(state["auto_restore_attempted"]),
        "restore_success": str(state["auto_restore_success"]),
        "diagnosis": state["diagnosis"],
        "action_plan": state["action_plan"]
    })
    
    return {"email_subject": subject, "email_body": response.content}


def dispatch_notification(state: AgentState) -> Dict[str, Any]:
    """Node: Save notification to local markdown file and optionally email via SMTP."""
    subject = state["email_subject"]
    body = state["email_body"]
    
    # Ensure alerts directory exists
    raw_alerts_dir = config.get("alerts_dir", "./alerts")
    # Resolve relative to watchdog directory if not absolute
    if not os.path.isabs(raw_alerts_dir):
        alerts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), raw_alerts_dir))
    else:
        alerts_dir = raw_alerts_dir
        
    os.makedirs(alerts_dir, exist_ok=True)
    
    # Save as Markdown file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    alert_filename = f"alert_{timestamp}.md"
    alert_path = os.path.join(alerts_dir, alert_filename)
    
    full_markdown_content = f"# {subject}\n\n*Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n{body}"
    
    with open(alert_path, "w") as f:
        f.write(full_markdown_content)
        
    print(f"\n[Watchdog Agent] Alert saved locally to: {alert_path}")
    
    # Attempt email dispatch if SMTP is enabled
    email_config = config.get("email", {})
    sent_via_email = False
    
    if email_config.get("smtp_enabled", False):
        try:
            print("[Watchdog Agent] Dispatching email via SMTP...")
            msg = MIMEMultipart()
            msg['From'] = email_config.get("from_address", "watchdog@example.com")
            msg['To'] = email_config.get("to_address", "admin@example.com")
            msg['Subject'] = subject
            
            # Attach body as plain text or html-rendered markdown.
            # Using plain text containing the markdown body for simplicity.
            msg.attach(MIMEText(body, 'plain'))
            
            server = smtplib.SMTP(email_config.get("smtp_host", "smtp.gmail.com"), email_config.get("smtp_port", 587))
            server.starttls()
            
            user = os.environ.get("WATCHDOG_SMTP_USER") or email_config.get("smtp_user", "")
            password = os.environ.get("WATCHDOG_SMTP_PASSWORD") or email_config.get("smtp_password", "")
            if user and password:
                server.login(user, password)
                
            server.sendmail(msg['From'], msg['To'], msg.as_string())
            server.quit()
            print("[Watchdog Agent] Email dispatch successful!")
            sent_via_email = True
        except Exception as e:
            print(f"[Watchdog Agent] SMTP Error: Failed to send email alert: {e}", file=sys.stderr)
            
    return {"notification_sent": True}

# -------------------------------------------------------------
# Construct the LangGraph Workflow
# -------------------------------------------------------------

builder = StateGraph(AgentState)

# Add nodes
builder.add_node("diagnose", diagnose_failure)
builder.add_node("recommend", recommend_action)
builder.add_node("draft_email", draft_email_alert)
builder.add_node("dispatch", dispatch_notification)

# Add edges
builder.add_edge(START, "diagnose")
builder.add_edge("diagnose", "recommend")
builder.add_edge("recommend", "draft_email")
builder.add_edge("draft_email", "dispatch")
builder.add_edge("dispatch", END)

# Compile the graph
agent_graph = builder.compile()

def run_diagnostics(
    gateway_status: str, 
    cron_status: str, 
    new_errors: List[str], 
    auto_restore_attempted: bool, 
    auto_restore_success: bool
) -> Dict[str, Any]:
    """Run the AI diagnostics workflow on the current failure state."""
    initial_state: AgentState = {
        "gateway_status": gateway_status,
        "cron_status": cron_status,
        "new_errors": new_errors,
        "auto_restore_attempted": auto_restore_attempted,
        "auto_restore_success": auto_restore_success,
        "diagnosis": "",
        "action_plan": "",
        "email_subject": "",
        "email_body": "",
        "notification_sent": False
    }
    
    print("[Watchdog Agent] Triggering LangGraph AI Diagnostics Workflow...")
    final_state = agent_graph.invoke(initial_state)
    return final_state

if __name__ == "__main__":
    # Test script standalone execution
    test_errors = [
        "2026-05-23 12:00:00,105 ERROR tools: Exception in SerperDevTool.run: HTTP 403 Forbidden - Invalid API Key",
        "2026-05-23 12:00:02,112 CRITICAL cron: Cron job 'daily_research_report' failed with traceback: File '/Users/aryamandev/watchdog/test.py', line 12, in run_report"
    ]
    res = run_diagnostics(
        gateway_status="Stopped",
        cron_status="Not Running (No Gateway)",
        new_errors=test_errors,
        auto_restore_attempted=True,
        auto_restore_success=False
    )
    print("\n--- STANDALONE RUN COMPLETED ---")
    print(f"Subject: {res['email_subject']}")
    print("Diagnosis:\n", res["diagnosis"])
    print("\nAction Plan:\n", res["action_plan"])
