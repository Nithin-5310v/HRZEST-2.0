# -*- coding: utf-8 -*-
"""AI HR Helpdesk Assistant Module with Fallback Ticket System.

Answers employee inquiries regarding leave policies, health benefits, and payroll timelines,
and provides automatic escalation to HR support tickets when queries are unresolved.
"""
from database import get_db_connection
from extensions import app_log
from utils.ai_client import call_claude

# RAG Policy Context Base
_POLICY_KNOWLEDGE_BASE = [
    {
        "category": "leave_policies",
        "topic": "Paid Time Off (PTO) & Sick Leave",
        "content": (
            "Employees receive 18 annual paid leave days per year, accrued monthly (1.5 days/month). "
            "Sick leave allows up to 10 paid days annually with a doctor's certificate required for >2 consecutive days. "
            "Casual leave requests must be submitted at least 24 hours in advance via the HRMS portal."
        )
    },
    {
        "category": "health_benefits",
        "topic": "Medical & Health Insurance",
        "content": (
            "Comprehensive health insurance covers employee, spouse, and up to 2 dependent children up to $50,000 annually. "
            "Dental and vision care are covered up to $1,500/year. Claims must be submitted within 30 days of medical service."
        )
    },
    {
        "category": "payroll_timelines",
        "topic": "Salaries, Overtime & Reimbursements",
        "content": (
            "Salaries are disbursed on the 28th of every month (or the preceding Friday if 28th falls on a weekend). "
            "Overtime is paid at 1.5x standard hourly rate for hours exceeding 40 hours/week. "
            "Expense reimbursements (travel, internet, office supplies) are processed on the 15th of each month."
        )
    },
    {
        "category": "workplace_rules",
        "topic": "Work From Home (WFH) & Hybrid Policy",
        "content": (
            "Hybrid work allows 2 remote days per week with manager pre-approval. Core business hours are 9:30 AM to 5:30 PM. "
            "Employees checking in after 9:45 AM are flagged as Late unless a grace period request is approved."
        )
    }
]


def _retrieve_relevant_policies(query):
    """Retrieve policy snippets matching the employee query keywords."""
    query_lower = query.lower()
    matches = []
    for item in _POLICY_KNOWLEDGE_BASE:
        words = item["category"].split("_") + item["topic"].lower().split()
        if any(w in query_lower for w in words) or any(w in query_lower for w in ["leave", "sick", "insurance", "salary", "pay", "wfh", "time"]):
            matches.append(f"[{item['topic']}]: {item['content']}")

    if not matches:
        matches = [f"[{item['topic']}]: {item['content']}" for item in _POLICY_KNOWLEDGE_BASE[:2]]

    return "\n\n".join(matches)


def _should_trigger_fallback(query, ai_response=""):
    """Check if query indicates high complexity or explicit request for human HR agent."""
    trigger_words = [
        "human", "talk to hr", "person", "representative", "manager", "dispute",
        "harassment", "salary error", "wrong pay", "urgent ticket", "complaint", "legal"
    ]
    query_lower = query.lower()
    if any(w in query_lower for w in trigger_words):
        return True
    if "unresolved" in ai_response.lower() or "cannot assist" in ai_response.lower():
        return True
    return False


def process_helpdesk_query(employee_id, query):
    """Process an employee HR query, return AI answer and check for ticket escalation fallback."""
    if not query:
        return {"answer": "Please enter your HR query or question.", "escalated": False}

    relevant_docs = _retrieve_relevant_policies(query)
    needs_escalation = _should_trigger_fallback(query)

    ai_answer = ""

    try:
        prompt = (
            f"You are the AI HR Helpdesk Assistant. Answer the employee's question strictly based on the "
            f"company policy document provided below.\n\n"
            f"Company Policy Documents:\n{relevant_docs}\n\n"
            f"Employee Question:\n{query}\n\n"
            f"Instructions: Give a clear, helpful, 2-3 sentence answer. If uncertain or if the question "
            f"involves a dispute/unresolved claim, recommend raising an HR ticket."
        )
        ai_answer = call_claude(prompt, max_tokens=400)
    except Exception as exc:
        app_log.warning("AI Helpdesk API fallback: %s", exc)

    if not ai_answer:
        # Structured fallback response
        if "leave" in query.lower() or "pto" in query.lower() or "vacation" in query.lower():
            ai_answer = ("Company policy grants 18 paid leave days and 10 sick days per year. Leave requests "
                         "can be submitted directly via the Leaves & Holidays tab.")
        elif "pay" in query.lower() or "salary" in query.lower() or "payslip" in query.lower():
            ai_answer = ("Salaries are processed on the 28th of each month. Payslips can be downloaded "
                         "under the Salary & Payslips section.")
        elif "health" in query.lower() or "insurance" in query.lower() or "medical" in query.lower():
            ai_answer = "Health insurance covers medical claims up to $50,000 annually. Submit claim forms to HR within 30 days of treatment."
        else:
            ai_answer = (f"I've searched our HR policies regarding '{query[:40]}'. For specific inquiries or "
                         "complex requests, I can route this directly to an HR support representative.")

    ticket_id = None
    if needs_escalation:
        ticket_id = _create_fallback_ticket(employee_id, query, ai_answer)

    return {
        "answer": ai_answer,
        "escalated": needs_escalation,
        "ticket_id": ticket_id,
        "suggested_actions": [
            "Submit Paid Leave Request",
            "View Payslip History",
            "Download Health Insurance Form"
        ]
    }


def _create_fallback_ticket(employee_id, query, ai_summary=""):
    """Auto-generate an HR Support Ticket in database when query requires human intervention."""
    try:
        db = get_db_connection()
        cur = db.cursor()
        subject = f"AI Escalation: {query[:50]}"
        category = "HR Policy / General"
        description = f"Automated escalation from AI HR Helpdesk Chatbot.\n\nUser Query: {query}\n\nAI Pre-Response: {ai_summary}"

        target_emp_id = None
        if employee_id:
            cur.execute("SELECT employee_id FROM employees WHERE employee_id=%s", (employee_id,))
            row = cur.fetchone()
            if row:
                target_emp_id = row[0]

        # tickets.employee_id is NOT NULL and this is an employee-facing
        # ticket system -- an unmatched id (e.g. an admin's username, which
        # never appears in employees.employee_id) must not silently file
        # the escalation against some unrelated employee found by falling
        # back to "whoever's first in the table".
        if not target_emp_id:
            cur.close()
            db.close()
            return None

        cur.execute(
            "INSERT INTO tickets (employee_id, category, subject, description, status, priority, created_at) "
            "VALUES (%s, %s, %s, %s, 'Open', 'High', NOW()) RETURNING id",
            (target_emp_id, category, subject, description)
        )
        row = cur.fetchone()
        ticket_id = row[0] if row else 1
        db.commit()
        from utils.helpers import invalidate_pending_counts_cache
        invalidate_pending_counts_cache()   # refresh the sidebar badges now (QA UX-01)
        cur.close()
        db.close()
        return ticket_id
    except Exception as e:
        app_log.error("Failed to create fallback ticket: %s", e)
        return None
