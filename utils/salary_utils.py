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

PAYROLL_CONFIG_SQL = ("SELECT pf_employee_pct, pf_employer_pct, professional_tax, tds_annual_pct, pf_basic_cap "
                      "FROM payroll_config LIMIT 1")
PAYROLL_CONFIG_DEFAULTS = {"pf_employee_pct": 12.0, "pf_employer_pct": 12.0, "professional_tax": 200.0,
                           "tds_annual_pct": 0.0, "pf_basic_cap": 15000.0}


def payroll_config_from_row(row):
    """The payroll_config row (PAYROLL_CONFIG_SQL) as a dict. Only a NULL
    column falls back to its default: a configured 0 stays 0 -- a company
    not registered for PF, or in a state without professional tax, sets
    them to 0, and `float(value or 12)` used to turn that back into 12% PF
    and Rs.200 PT on every payslip (QA BUG-40)."""
    if not row:
        return dict(PAYROLL_CONFIG_DEFAULTS)
    return {key: float(PAYROLL_CONFIG_DEFAULTS[key] if value is None else value)
            for key, value in zip(PAYROLL_CONFIG_DEFAULTS, row)}


def apply_statutory_deductions(entry, payroll_cfg=None):
    """The one net-pay calculation (owner decision NV-19, 2026-10-14; QA BUG-05).

    Earnings are what the month's attendance earns under the company's late
    and half-day rules (compute_salary_entry's full_earn + late_earn +
    half_earn) plus incentives -- the amount disbursed until now. The PF, PT
    and TDS from Payroll settings are deducted from that, computed as the
    payslip always did: PF on min(Basic, cap) with Basic = monthly CTC x
    basic %, PT a flat monthly amount, TDS = annual CTC x rate / 12. If they
    would exceed the earnings they are scaled down together, so net pay is
    never negative. The disbursement, the payslip and every screen use the
    resulting `net`, so a payslip shows exactly what is paid.

    Adds to `entry` (and returns it): monthly_ctc, basic_pct, basic,
    attendance_earned, earned, pf, pf_employer, pt, tds, statutory,
    attendance_ded (pay lost to absences, late and half days),
    deduction (attendance_ded + statutory) and net."""
    pc = dict(PAYROLL_CONFIG_DEFAULTS)
    pc.update(payroll_cfg or {})
    spd = float(entry.get("spd") or 0)
    monthly_ctc = float(entry.get("monthly_ctc") or 0)
    if monthly_ctc <= 0:
        monthly_ctc = round(spd * 26, 2)
    basic_pct = float(entry["basic_pct"]) if entry.get("basic_pct") is not None else 50.0
    basic = round(monthly_ctc * basic_pct / 100, 2)

    attendance_earned = round(float(entry.get("full_earn") or 0) + float(entry.get("late_earn") or 0)
                              + float(entry.get("half_earn") or 0), 2)
    earned = round(attendance_earned + float(entry.get("incentive") or 0), 2)

    pf_base = min(basic, float(pc["pf_basic_cap"]))
    pf = round(pf_base * float(pc["pf_employee_pct"]) / 100, 2)
    pf_employer = round(pf_base * float(pc["pf_employer_pct"]) / 100, 2)
    pt = round(float(pc["professional_tax"]), 2)
    tds = round(monthly_ctc * 12 * float(pc["tds_annual_pct"]) / 100 / 12, 2)
    statutory = pf + pt + tds
    if statutory > earned:
        ratio = earned / statutory if statutory > 0 else 0
        pf, pt, tds = (round(v * ratio, 2) for v in (pf, pt, tds))
    statutory = round(pf + pt + tds, 2)
    net = max(0.0, round(earned - statutory, 2))

    gross = float(entry.get("gross") if entry.get("gross") is not None else attendance_earned)
    attendance_ded = round(max(0.0, gross - attendance_earned), 2)
    entry.update({
        "monthly_ctc": monthly_ctc, "basic_pct": basic_pct, "basic": basic,
        "attendance_earned": attendance_earned, "earned": earned,
        "pf": pf, "pf_employer": pf_employer, "pt": pt, "tds": tds, "statutory": statutory,
        "attendance_ded": attendance_ded, "deduction": round(attendance_ded + statutory, 2),
        "net": net,
    })
    return entry


def build_salary_slip_html(emp_name, emp_id, emp_email, month_name, year, month, salary_data,
                           company_name="", emp_designation="", emp_dept="",
                           pan="", uan="", bank_account="", bank_name="",
                           payroll_cfg=None):
    # Every figure comes from the one net-pay calculation (QA BUG-05):
    # callers pass an entry from compute_salary_data_for_month(), which has
    # already applied it; a bare dict (unit tests) gets it applied here.
    e = salary_data if "statutory" in salary_data else apply_statutory_deductions(dict(salary_data), payroll_cfg)
    pc = dict(PAYROLL_CONFIG_DEFAULTS)
    pc.update(payroll_cfg or {})
    pf_pct = float(pc["pf_employee_pct"])
    pf_er_pct = float(pc["pf_employer_pct"])
    tds_ann_pct = float(pc["tds_annual_pct"])

    spd = float(e.get("spd") or 0)
    monthly_ctc = float(e["monthly_ctc"])
    basic = float(e["basic"])
    full_d = int(e.get("full_days", 0))
    late_d = int(e.get("late_days", 0))
    half_d = int(e.get("half_days", 0))
    absent_d = int(e.get("absent", 0) or 0)
    paid_days_display = full_d + late_d + half_d
    billable = e.get("billable", paid_days_display + absent_d)
    full_earn = float(e.get("full_earn") or 0)
    late_earn = float(e.get("late_earn") or 0)
    half_earn = float(e.get("half_earn") or 0)
    earned = float(e["earned"])
    pf_ded, pf_er_ded, pt_monthly, tds_ded = float(e["pf"]), float(e["pf_employer"]), float(e["pt"]), float(e["tds"])
    total_ded = float(e["statutory"])
    attendance_ded = float(e["attendance_ded"])
    net_pay = float(e["net"])

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
        incentive_row = f'<tr><td>Incentive / Bonus</td><td class="green">+ Rs. {e["incentive"]:.2f}</td></tr>'

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
      <tr><td>Working Days</td><td>{billable}</td></tr>
      <tr><td>Days Present</td><td>{paid_days_display}</td></tr>
      <tr><td>Absent Days</td><td>{absent_d}</td></tr>
      <tr><td>Monthly CTC</td><td>Rs. {monthly_ctc:,.2f}</td></tr>
      <tr><td>Basic (PF base)</td><td>Rs. {basic:,.2f}</td></tr>
    </table>
  </div>

  <div class="att-strip">
    <div class="att-cell"><div class="num green">{full_d}</div><div class="lbl">Full Days</div></div>
    <div class="att-cell"><div class="num yellow">{late_d}</div><div class="lbl">Late Days</div></div>
    <div class="att-cell"><div class="num yellow">{half_d}</div><div class="lbl">Half Days</div></div>
    <div class="att-cell"><div class="num red">{absent_d}</div><div class="lbl">Absent</div></div>
    <div class="att-cell"><div class="num" style="color:#3b82f6">{e.get('holiday_days', 0)}</div><div class="lbl">Holidays</div></div>
    <div class="att-cell"><div class="num" style="color:#9333ea">{e.get('leave_days', 0)}</div><div class="lbl">Leave (Paid)</div></div>
  </div>

  <div class="body">
    <div class="two-col">
      <div>
        <div class="sec-title">Earnings</div>
        <table class="pay-tbl">
          <tr><td>Full Days ({full_d} × Rs.{spd:,.2f})</td><td>Rs. {full_earn:,.2f}</td></tr>
          <tr><td>Late Days ({late_d}, after late deduction)</td><td>Rs. {late_earn:,.2f}</td></tr>
          <tr><td>Half Days ({half_d}, after half-day deduction)</td><td>Rs. {half_earn:,.2f}</td></tr>
          {incentive_row}
          <tr class="tot"><td>Gross Earned</td><td>Rs. {earned:,.2f}</td></tr>
        </table>
        <div style="margin-top:10px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 12px;font-size:12px;color:#15803d;">
          <b>PF -- Employer Contribution ({pf_er_pct:.0f}%)</b>: Rs. {pf_er_ded:,.2f}
          <div style="color:#64748b;margin-top:2px;font-size:11px;">Company's share -- not deducted from your pay</div>
        </div>
      </div>
      <div>
        <div class="sec-title">Deductions</div>
        <table class="pay-tbl">
          <tr><td>PF -- Employee Contribution ({pf_pct:.0f}% of Basic)</td><td class="red">Rs. {pf_ded:,.2f}</td></tr>
          <tr><td>Professional Tax</td><td class="red">Rs. {pt_monthly:,.2f}</td></tr>
          {"<tr><td>TDS -- Income Tax (annual " + f"{tds_ann_pct:.1f}%" + ")</td><td class='red'>Rs. " + f"{tds_ded:,.2f}</td></tr>" if tds_ded > 0 else ""}
          <tr class="tot"><td>Total Deductions</td><td>Rs. {total_ded:,.2f}</td></tr>
        </table>
        <div style="margin-top:10px;background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px 12px;font-size:12px;color:#92400e;">
          <b>Pay lost to absences, late and half days</b>: Rs. {attendance_ded:,.2f}
          <div style="color:#64748b;margin-top:2px;font-size:11px;">Already reflected in the earnings</div>
        </div>
      </div>
    </div>

    <div class="net-box">
      <div>
        <div class="lbl">Net Take-Home Pay</div>
        <div style="font-size:11px;opacity:.65;margin-top:4px;">
          Rs.{earned:,.2f} earned − PF Rs.{pf_ded:,.2f} − PT Rs.{pt_monthly:,.2f}{f" − TDS Rs.{tds_ded:,.2f}" if tds_ded > 0 else ""}
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
