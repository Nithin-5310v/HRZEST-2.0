"""Tiny shared CSV-response helper, extracted from blueprints/platform_admin.py
so blueprints/company_profile.py's payment-history export doesn't duplicate it."""
import io
import csv
from flask import Response


def csv_response(filename, header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
