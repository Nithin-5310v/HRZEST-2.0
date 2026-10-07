# -*- coding: utf-8 -*-
"""Salary calculation and payslip rendering -- pure functions, no DB access.

Extracted from app.py as part of migrating the payroll routes into
blueprints/payroll.py -- payroll.py's manifest comment already flagged this
as the eventual plan ("stays in app.py until extracted to
utils/salary_utils.py"). Doing it now rather than having the blueprint
import back from app.py, which would create a real circular-import
dependency: wsgi.py needs to import the blueprint before or during
app.py's own import, and app.py importing utils.helpers/utils.auth/etc.
already happens at its own module top level.
"""
import html as _html
import datetime
import utils.config as cfg
from utils.attendance_utils import infer_type_legacy
from utils.dlp import mask_tail


def build_salary_slip_html(emp_name, emp_id, emp_email, month_name, year, month, salary_data,
                           company_name="", emp_designation="", emp_dept="",
                           pan="", uan="", bank_account="", bank_name="",
                           payroll_cfg=None):
    e = salary_data
    pc = payroll_cfg or {}

    # ── Salary structure ──────────────────────────────────────────
    monthly_ctc = float(e.get("monthly_ctc", 0))
    basic_pct = int(e.get("basic_pct", 50))
    if monthly_ctc <= 0 and float(e.get("spd", 0)) > 0:
        monthly_ctc = round(float(e["spd"]) * 26, 2)

    basic = round(monthly_ctc * basic_pct / 100, 2)
    hra = round(monthly_ctc * 0.20, 2)
    # Cap conveyance so gross never exceeds CTC
    conveyance = round(min(1600.0, max(0, monthly_ctc - basic - hra)), 2)
    special_all = round(max(0, monthly_ctc - basic - hra - conveyance), 2)
    gross_salary = round(basic + hra + conveyance + special_all, 2)

    # ── LOP: standard 26-day denominator (Indian payroll norm) ───
    full_d = int(e.get("full_days", 0))
    late_d = int(e.get("late_days", 0))
    half_d = int(e.get("half_days", 0))
    lop_days = float(e.get("absent", 0))
    paid_days_display = full_d + late_d + half_d   # integer count for display
    lop_ded = round(gross_salary / 26 * lop_days, 2)
    gross_earned = round(gross_salary - lop_ded, 2)

    # ── Statutory deductions ─────────────────────────────────────
    pf_pct = float(pc.get("pf_employee_pct", 12))
    pf_er_pct = float(pc.get("pf_employer_pct", 12))
    pf_cap_basic = float(pc.get("pf_basic_cap", 15000))
    pt_monthly = float(pc.get("professional_tax", 200))
    tds_ann_pct = float(pc.get("tds_annual_pct", 0))

    # PF on capped basic; TDS = annual taxable (CTC×12) × rate ÷ 12
    pf_ded = round(min(basic, pf_cap_basic) * pf_pct / 100, 2)
    pf_er_ded = round(min(basic, pf_cap_basic) * pf_er_pct / 100, 2)
    annual_ctc = monthly_ctc * 12
    tds_ded = round(annual_ctc * tds_ann_pct / 100 / 12, 2)
    # Cap statutory deductions to gross earned (net cannot go below 0)
    stat_ded = pf_ded + pt_monthly + tds_ded
    if stat_ded > gross_earned:
        ratio = gross_earned / stat_ded if stat_ded > 0 else 0
        pf_ded = round(pf_ded * ratio, 2)
        pt_monthly = round(pt_monthly * ratio, 2)
        tds_ded = round(tds_ded * ratio, 2)
    total_ded = round(lop_ded + pf_ded + pt_monthly + tds_ded, 2)
    net_pay = max(0, round(gross_earned - pf_ded - pt_monthly - tds_ded, 2))

    emp_row_extra = ""
    if emp_designation:
        emp_row_extra += f"<tr><td>Designation</td><td>{_html.escape(str(emp_designation))}</td></tr>"
    if emp_dept:
        emp_row_extra += f"<tr><td>Department</td><td>{_html.escape(str(emp_dept))}</td></tr>"
    if pan:
        emp_row_extra += f"<tr><td>PAN</td><td>{_html.escape(str(pan))}</td></tr>"
    if uan:
        emp_row_extra += f"<tr><td>UAN</td><td>{_html.escape(str(uan))}</td></tr>"
    if bank_account:
        masked = mask_tail(bank_account)
        emp_row_extra += f"<tr><td>Bank A/C</td><td>{_html.escape(masked)}</td></tr>"
    if bank_name:
        emp_row_extra += f"<tr><td>Bank</td><td>{_html.escape(str(bank_name))}</td></tr>"

    incentive_row = ""
    if e.get("incentive", 0) > 0:
        incentive_row = f'<tr><td>Incentive / Bonus</td><td class="green">+ Rs. {e["incentive"]:.2f}</td><td></td><td></td></tr>'

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:"Segoe UI",Arial,sans-serif;background:#f0f4ff;color:#1e293b}}
  .wrap{{max-width:800px;margin:20px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 8px 40px rgba(0,0,0,.12)}}
  .hdr{{background:linear-gradient(135deg,#0f2460,#1e3a8a);padding:28px 32px;color:#fff;display:flex;justify-content:space-between;align-items:center}}
  .hdr-left h1{{font-size:20px;font-weight:800;margin-bottom:4px}}
  .hdr-left p{{font-size:13px;opacity:.75}}
  .hdr-right{{text-align:right}}
  .hdr-right .slip-num{{font-size:12px;opacity:.7;margin-bottom:4px}}
  .hdr-right .month{{font-size:18px;font-weight:700}}
  .emp-bar{{background:#dbeafe;padding:16px 32px;display:grid;grid-template-columns:1fr 1fr;gap:4px 40px;font-size:13px}}
  .emp-bar td:first-child{{font-weight:700;color:#1e3a8a;white-space:nowrap}}
  .emp-bar td{{padding:3px 6px;color:#1e293b}}
  .att-strip{{display:grid;grid-template-columns:repeat(6,1fr);gap:0;border-bottom:1px solid #e2e8f0}}
  .att-cell{{text-align:center;padding:14px 8px;border-right:1px solid #e2e8f0}}
  .att-cell:last-child{{border-right:none}}
  .att-cell .num{{font-size:22px;font-weight:800}}
  .att-cell .lbl{{font-size:10px;color:#64748b;margin-top:2px;text-transform:uppercase;letter-spacing:.3px}}
  .body{{padding:24px 32px}}
  .two-col{{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:16px}}
  .sec-title{{font-size:12px;font-weight:800;color:#1e3a8a;text-transform:uppercase;letter-spacing:.5px;
    padding-bottom:7px;border-bottom:2px solid #dbeafe;margin-bottom:10px}}
  table.pay-tbl{{width:100%;border-collapse:collapse;font-size:13px}}
  table.pay-tbl td{{padding:7px 10px;border-bottom:1px solid #f1f5f9;vertical-align:top}}
  table.pay-tbl td:last-child{{text-align:right;font-weight:600;white-space:nowrap}}
  table.pay-tbl tr.tot td{{background:#f8fafc;font-weight:800;border-top:2px solid #dbeafe;border-bottom:2px solid #dbeafe;font-size:14px}}
  .net-box{{background:linear-gradient(135deg,#0f2460,#1e3a8a);color:#fff;border-radius:12px;padding:18px 24px;
    display:flex;justify-content:space-between;align-items:center;margin-top:16px}}
  .net-box .lbl{{font-size:13px;opacity:.8}}
  .net-box .amt{{font-size:28px;font-weight:900}}
  .footer{{background:#f8fafc;padding:14px 32px;text-align:center;font-size:11px;color:#94a3b8;border-top:1px solid #e2e8f0;
    display:flex;justify-content:space-between;align-items:center}}
  .print-btn{{display:flex;gap:10px;margin:18px 32px 4px;justify-content:flex-end}}
  .btn{{padding:9px 20px;border:none;border-radius:9px;font-size:13px;font-weight:700;cursor:pointer}}
  .btn-print{{background:#0f2460;color:#fff}}
  .btn-back{{background:#f1f5f9;color:#64748b}}
  .green{{color:#16a34a}} .red{{color:#ef4444}} .yellow{{color:#f59e0b}}
  @media print{{
    body{{background:#fff}}
    .wrap{{box-shadow:none;margin:0;border-radius:0}}
    .print-btn{{display:none}}
    .btn{{display:none}}
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="print-btn">
    <button class="btn btn-back" onclick="history.back()">&#8592; Back</button>
    <button class="btn btn-print" onclick="window.print()">&#128438; Download / Print PDF</button>
  </div>

  <div class="hdr">
    <div class="hdr-left">
      <h1>{_html.escape(str(company_name)) if company_name else "Payslip"}</h1>
      <p>Salary Slip -- {month_name}</p>
    </div>
    <div class="hdr-right">
      <div class="slip-num">Slip ID: {_html.escape(str(emp_id))}-{year}{month:02d}</div>
      <div class="month">{month_name}</div>
    </div>
  </div>

  <div class="emp-bar">
    <table>
      <tr><td>Employee Name</td><td>{_html.escape(str(emp_name))}</td></tr>
      <tr><td>Employee ID</td><td>{_html.escape(str(emp_id))}</td></tr>
      <tr><td>Email</td><td>{_html.escape(str(emp_email)) if emp_email else 'N/A'}</td></tr>
      {emp_row_extra}
    </table>
    <table>
      <tr><td>Pay Period</td><td>{month_name}</td></tr>
      <tr><td>Working Days (Standard)</td><td>26</td></tr>
      <tr><td>Days Present</td><td>{paid_days_display}</td></tr>
      <tr><td>LOP Days</td><td>{int(lop_days)}</td></tr>
      <tr><td>Monthly CTC</td><td>Rs. {monthly_ctc:,.2f}</td></tr>
    </table>
  </div>

  <div class="att-strip">
    <div class="att-cell"><div class="num green">{full_d}</div><div class="lbl">Full Days</div></div>
    <div class="att-cell"><div class="num yellow">{late_d}</div><div class="lbl">Late Days</div></div>
    <div class="att-cell"><div class="num yellow">{half_d}</div><div class="lbl">Half Days</div></div>
    <div class="att-cell"><div class="num red">{int(lop_days)}</div><div class="lbl">LOP / Absent</div></div>
    <div class="att-cell"><div class="num" style="color:#3b82f6">{e.get('holiday_days', 0)}</div><div class="lbl">Holidays</div></div>
    <div class="att-cell"><div class="num" style="color:#9333ea">{e.get('leave_days', 0)}</div><div class="lbl">Leave (Paid)</div></div>
  </div>

  <div class="body">
    <div class="two-col">
      <div>
        <div class="sec-title">Earnings (Monthly)</div>
        <table class="pay-tbl">
          <tr><td>Basic Salary ({basic_pct}% of CTC)</td><td>Rs. {basic:,.2f}</td></tr>
          <tr><td>House Rent Allowance (HRA)</td><td>Rs. {hra:,.2f}</td></tr>
          <tr><td>Conveyance Allowance</td><td>Rs. {conveyance:,.2f}</td></tr>
          <tr><td>Special Allowance</td><td>Rs. {special_all:,.2f}</td></tr>
          {incentive_row}
          <tr class="tot"><td>Gross Salary</td><td>Rs. {gross_salary:,.2f}</td></tr>
        </table>
        <div style="margin-top:10px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 12px;font-size:12px;color:#15803d;">
          <b>PF -- Employer Contribution ({pf_er_pct:.0f}%)</b>: Rs. {pf_er_ded:,.2f}
          <div style="color:#64748b;margin-top:2px;font-size:11px;">Company's share -- not deducted from your pay</div>
        </div>
      </div>
      <div>
        <div class="sec-title">Deductions</div>
        <table class="pay-tbl">
          <tr><td>Loss of Pay -- LOP ({int(lop_days)} days × Rs.{gross_salary/26:,.2f})</td><td class="red">Rs. {lop_ded:,.2f}</td></tr>
          <tr><td>PF -- Employee Contribution ({pf_pct:.0f}% of Basic)</td><td class="red">Rs. {pf_ded:,.2f}</td></tr>
          <tr><td>Professional Tax</td><td class="red">Rs. {pt_monthly:,.2f}</td></tr>
          {"<tr><td>TDS -- Income Tax (annual " + f"{tds_ann_pct:.1f}%" + ")</td><td class='red'>Rs. " + f"{tds_ded:,.2f}</td></tr>" if tds_ded > 0 else ""}
          <tr class="tot"><td>Total Deductions</td><td>Rs. {total_ded:,.2f}</td></tr>
        </table>
        <div style="margin-top:10px;background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px 12px;font-size:12px;color:#92400e;">
          <b>Gross Earned</b> (after LOP): Rs. {gross_earned:,.2f}
          <div style="color:#64748b;margin-top:2px;font-size:11px;">Gross Salary − LOP before other deductions</div>
        </div>
      </div>
    </div>

    <div class="net-box">
      <div>
        <div class="lbl">Net Take-Home Pay</div>
        <div style="font-size:11px;opacity:.65;margin-top:4px;">
          Rs.{gross_salary:,.2f} − LOP Rs.{lop_ded:,.2f} − PF Rs.{pf_ded:,.2f} − PT Rs.{pt_monthly:,.2f}{f" − TDS Rs.{tds_ded:,.2f}" if tds_ded > 0 else ""}
        </div>
      </div>
      <div class="amt">Rs. {net_pay:,.2f}</div>
    </div>
  </div>

  <div class="footer">
    <span>This is a system-generated payslip. Contact HR for any discrepancies.</span>
    <span>Generated on {datetime.date.today().strftime('%d %B %Y')}</span>
  </div>
</div>
</body>
</html>"""


def compute_salary_entry(emp_id, name, spd, att_map, billable_past,
                         holidays_set=None, leave_dates=None):
    if holidays_set is None:
        holidays_set = set()
    if leave_dates is None:
        leave_dates = set()

    rules = cfg.salary_rules()
    emp_att = att_map.get(emp_id, {})
    full_days = half_days = late_days = absent_days = 0
    holiday_days = leave_days_count = 0

    for d in billable_past:
        if d in holidays_set:
            if rules.holiday_pay == 'paid':
                full_days += 1
            else:
                absent_days += 1   # unpaid holiday = counts as absent deduction
            holiday_days += 1
        elif d in leave_dates:
            if rules.leave_pay == 'absent':
                absent_days += 1   # count approved leave as absent
            else:
                leave_days_count += 1  # exclude from working days, no pay/no deduction
        else:
            row = emp_att.get(d)
            if row:
                _, _, login_t, logout_t, status, _logout_status, att_type = row
                final = att_type if att_type else infer_type_legacy(status, login_t, logout_t)
                if final == "Full Day":
                    full_days += 1
                elif final == "Late - Full Day":
                    late_days += 1
                elif final in ("Half Day", "Present"):
                    half_days += 1
                else:
                    absent_days += 1
            else:
                absent_days += 1

    effective_billable = len(billable_past) - leave_days_count

    spd_f = float(spd)
    full_earn = round(full_days * spd_f, 2)
    late_earn = round(late_days * spd_f * (1 - rules.late_deduction_rate), 2)
    half_earn = round(half_days * spd_f * (1 - rules.half_day_rate), 2)
    net = round(full_earn + late_earn + half_earn, 2)
    gross = round(spd_f * effective_billable, 2)
    deduction = round(gross - net, 2)

    return {
        "emp_id": emp_id,
        "name": name,
        "spd": round(spd_f, 2),
        "billable": effective_billable,
        "holiday_days": holiday_days,
        "leave_days": leave_days_count,
        "full_days": full_days,
        "half_days": half_days,
        "late_days": late_days,
        "absent": absent_days,
        "full_earn": full_earn,
        "late_earn": late_earn,
        "half_earn": half_earn,
        "gross": gross,
        "absent_ded": round(absent_days * spd_f, 2),
        "half_ded": round(half_days * spd_f * rules.half_day_rate, 2),
        "late_ded": round(late_days * spd_f * rules.late_deduction_rate, 2),
        "deduction": deduction,
        "net": net,
    }
