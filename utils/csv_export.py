"""Tiny shared CSV-response helper, extracted from blueprints/platform_admin.py
so blueprints/company_profile.py's payment-history export doesn't duplicate it."""
import io
import csv
from flask import Response

# A cell starting with one of these is run as a formula by Excel/Sheets/Calc
# (CSV/formula injection). Values such as a self-chosen company name end up in
# exports opened by admins (security review 2026-11-20).
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def spreadsheet_safe(value):
    """A string that would be read as a formula gets a leading apostrophe;
    every other value (numbers, Decimals, dates, None) is returned as is, so
    numeric cells stay numeric."""
    if isinstance(value, str) and value.startswith(_FORMULA_LEAD):
        return "'" + value
    return value


def csv_response(filename, header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([spreadsheet_safe(h) for h in header])
    writer.writerows([spreadsheet_safe(v) for v in row] for row in rows)
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
