"""Writes city.mmdb and asn.mmdb next to this file: tiny GeoIP databases in
the same shape as DB-IP's "IP to City Lite" / "IP to ASN Lite" files, for
tests/test_ip_geo.py and the SOC console tests. The places are real, the
network names are made up.

The .mmdb files are committed, so CI doesn't need the writer. To change them:

    pip install mmdb-writer
    python tests/fixtures/geoip/make_fixtures.py
"""
import os

from mmdb_writer import MMDBWriter, MmdbU32
from netaddr import IPSet

HERE = os.path.dirname(os.path.abspath(__file__))


def place(city, region, country, code, lat, lon):
    record = {
        "continent": {"code": "EU" if code == "GB" else "AS" if code in ("IN", "JP") else "NA"},
        "country": {"iso_code": code, "names": {"en": country}},
        "location": {"latitude": lat, "longitude": lon},
    }
    if city:
        record["city"] = {"names": {"en": city}}
    if region:
        record["subdivisions"] = [{"names": {"en": region}}]
    return record


CITY = [
    ("81.2.69.0/24", place("London", "England", "United Kingdom", "GB", 51.5085, -0.1257)),
    ("8.8.8.0/24", place("Mountain View", "California", "United States", "US", 37.4056, -122.0775)),
    ("45.33.32.0/24", place("Fremont", "California", "United States", "US", 37.5483, -121.9886)),
    ("49.204.0.0/16", place("Bengaluru", "Karnataka", "India", "IN", 12.9716, 77.5946)),
    ("2001:218::/32", place(None, None, "Japan", "JP", 35.69, 139.69)),
]
ASN = [
    ("81.2.69.0/24", 64501, "Example Transit Ltd"),
    ("8.8.8.0/24", 64502, "Example Public DNS"),
    ("45.33.32.0/20", 64503, "Example Cloud Hosting"),
    ("49.204.0.0/14", 64504, "Example Broadband India"),
]


def write(path, db_type, rows):
    writer = MMDBWriter(ip_version=6, ipv4_compatible=True, database_type=db_type, languages=["en"],
                        description={"en": "HRZEST test fixture -- not real GeoIP data"})
    for network, record in rows:
        writer.insert_network(IPSet([network]), record)
    writer.to_db_file(path)


if __name__ == "__main__":
    write(os.path.join(HERE, "city.mmdb"), "DBIP-City-Lite", CITY)
    write(os.path.join(HERE, "asn.mmdb"), "DBIP-ASN-Lite (compat=GeoLite2-ASN)",
          [(net, {"autonomous_system_number": MmdbU32(n), "autonomous_system_organization": org})
           for net, n, org in ASN])
    print("wrote", os.path.join(HERE, "city.mmdb"), "and asn.mmdb")
