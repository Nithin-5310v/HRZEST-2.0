"""utils/ip_geo.py: approximate IP locations for the SOC console, read from
GeoIP files on the server. The databases here are tests/fixtures/geoip/*.mmdb
(tiny DB-IP-shaped files written by make_fixtures.py)."""
import os
import shutil

import pytest

from utils import ip_geo

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "geoip")

LONDON = {
    "scope": "public", "country_code": "GB", "country": "United Kingdom", "region": "England", "city": "London",
    "latitude": 51.5085, "longitude": -0.1257, "accuracy_km": None,
    "asn": 64501, "org": "Example Transit Ltd", "network": "81.2.69.0/24",
}


@pytest.fixture
def geo(monkeypatch, tmp_path):
    """Point ip_geo at an empty folder, re-read on every call; geo('city.mmdb', ...)
    copies fixture databases into it."""
    monkeypatch.setenv("GEOIP_DIR", str(tmp_path))
    monkeypatch.delenv("GEOIP_CITY_DB", raising=False)
    monkeypatch.delenv("GEOIP_ASN_DB", raising=False)
    monkeypatch.setattr(ip_geo, "_RECHECK_SECONDS", 0)
    ip_geo.reset()

    def add(*names):
        for name in names:
            shutil.copy(os.path.join(FIXTURES, name), os.path.join(str(tmp_path), name))
    add.dir = tmp_path
    yield add
    ip_geo.reset()


class TestLookup:
    def test_public_address_gets_place_and_network(self, geo):
        geo("city.mmdb", "asn.mmdb")
        assert ip_geo.lookup("81.2.69.142") == LONDON

    def test_ipv6_and_ipv4_mapped_addresses(self, geo):
        geo("city.mmdb", "asn.mmdb")
        jp = ip_geo.lookup("2001:218:1::1")
        assert (jp["country_code"], jp["country"], jp["city"], jp["asn"]) == ("JP", "Japan", None, None)
        assert ip_geo.lookup("::ffff:81.2.69.142") == LONDON
        assert ip_geo.lookup(" 81.2.69.142 ") == LONDON

    def test_public_address_the_databases_dont_know(self, geo):
        geo("city.mmdb", "asn.mmdb")
        got = ip_geo.lookup("1.1.1.1")
        assert got["scope"] == "public"
        assert all(v is None for k, v in got.items() if k != "scope")

    @pytest.mark.parametrize("ip,scope", [
        ("10.1.2.3", "private"), ("172.16.0.1", "private"), ("192.168.1.10", "private"), ("fd12:3456::1", "private"),
        ("127.0.0.1", "loopback"), ("::1", "loopback"), ("100.64.0.1", "shared"),
        ("169.254.10.1", "link-local"), ("fe80::1", "link-local"), ("224.0.0.1", "multicast"),
        ("203.0.113.9", "reserved"), ("198.51.100.7", "reserved"), ("192.0.2.1", "reserved"), ("240.0.0.1", "reserved"),
    ])
    def test_addresses_with_no_public_location_say_what_they_are(self, geo, ip, scope):
        # Answered without any database: there is nothing to look up.
        assert ip_geo.lookup(ip) == {"scope": scope}

    @pytest.mark.parametrize("text", ["nonsense", "", None, "81.2.69.0/24", "999.1.1.1"])
    def test_text_that_is_not_an_address(self, geo, text):
        geo("city.mmdb", "asn.mmdb")
        assert ip_geo.lookup(text) is None

    def test_no_database_installed(self, geo):
        assert ip_geo.lookup("81.2.69.142") is None
        assert ip_geo.status() == {"available": False, "databases": [], "attribution": []}

    def test_only_the_network_database(self, geo):
        geo("asn.mmdb")
        got = ip_geo.lookup("49.204.10.1")
        assert (got["city"], got["country_code"]) == (None, None)
        assert (got["asn"], got["org"], got["network"]) == (64504, "Example Broadband India", "49.204.0.0/14")
        assert [d["kind"] for d in ip_geo.status()["databases"]] == ["asn"]

    def test_file_names_can_be_given_directly(self, geo, monkeypatch):
        monkeypatch.setenv("GEOIP_CITY_DB", os.path.join(FIXTURES, "city.mmdb"))
        monkeypatch.setenv("GEOIP_ASN_DB", os.path.join(FIXTURES, "asn.mmdb"))
        assert ip_geo.lookup("81.2.69.142") == LONDON


class TestFilesOnDisk:
    def test_a_database_added_later_is_used_without_a_restart(self, geo):
        geo("asn.mmdb")
        assert ip_geo.lookup("81.2.69.142")["city"] is None
        geo("city.mmdb")
        assert ip_geo.lookup("81.2.69.142") == LONDON      # and the cached answer was dropped

    def test_files_are_rechecked_only_once_a_minute(self, geo, monkeypatch):
        monkeypatch.setattr(ip_geo, "_RECHECK_SECONDS", 60)
        ip_geo.reset()
        assert ip_geo.lookup("81.2.69.142") is None
        geo("city.mmdb", "asn.mmdb")
        assert ip_geo.lookup("81.2.69.142") is None        # not stat()ed again yet
        ip_geo.reset()
        assert ip_geo.lookup("81.2.69.142") == LONDON

    def test_an_unreadable_file_is_skipped_not_fatal(self, geo, caplog):
        geo("asn.mmdb")
        (geo.dir / "city.mmdb").write_bytes(b"not a GeoIP database" * 50)
        got = ip_geo.lookup("81.2.69.142")
        assert got["city"] is None and got["org"] == "Example Transit Ltd"
        assert "could not be opened" in caplog.text
        assert [d["kind"] for d in ip_geo.status()["databases"]] == ["asn"]


class TestBatchAndStatus:
    def test_lookup_many_keys_by_the_text_sent_and_caps_the_batch(self, geo, monkeypatch):
        geo("city.mmdb", "asn.mmdb")
        monkeypatch.setattr(ip_geo, "MAX_BATCH", 3)
        got = ip_geo.lookup_many(["81.2.69.142", "not-an-ip", "81.2.69.142", 42, "10.0.0.1", "8.8.8.8", "49.204.10.1"])
        assert list(got) == ["81.2.69.142", "10.0.0.1", "8.8.8.8"]
        assert got["81.2.69.142"] == LONDON and got["10.0.0.1"] == {"scope": "private"}
        assert got["8.8.8.8"]["city"] == "Mountain View"

    def test_status_names_the_databases_their_age_and_the_credit_owed(self, geo, monkeypatch):
        geo("city.mmdb", "asn.mmdb")
        st = ip_geo.status()
        assert st["available"] is True
        assert [(d["kind"], d["provider"], d["type"]) for d in st["databases"]] == [
            ("city", "DB-IP", "DBIP-City-Lite"), ("asn", "DB-IP", "DBIP-ASN-Lite (compat=GeoLite2-ASN)")]
        assert all(len(d["built"]) == 10 and d["age_days"] >= 0 for d in st["databases"])
        assert st["attribution"] == [{"text": "IP geolocation by DB-IP", "url": "https://db-ip.com"}]
        monkeypatch.setattr(ip_geo, "STALE_DAYS", 10 ** 6)
        assert not any(d["stale"] for d in ip_geo.status()["databases"])
        monkeypatch.setattr(ip_geo, "STALE_DAYS", -1)
        assert all(d["stale"] for d in ip_geo.status()["databases"])


class TestRecordParsing:
    def test_odd_records_never_raise_and_text_is_bounded(self):
        assert ip_geo._place({"country": "GB", "subdivisions": [], "city": {"names": "x"}, "location": [1, 2]}) == {
            "country_code": None, "country": None, "region": None, "city": None,
            "latitude": None, "longitude": None, "accuracy_km": None}
        assert ip_geo._place({"subdivisions": {"names": {"en": "Kent"}}})["region"] is None
        assert ip_geo._place("not a record")["country"] is None
        long_name = ip_geo._place({"city": {"names": {"en": "x" * 500}}, "location": {"latitude": True}})
        assert len(long_name["city"]) == ip_geo._TEXT_MAX and long_name["latitude"] is None
        assert ip_geo._network({"autonomous_system_number": "64500", "autonomous_system_organization": 7}, 24, "81.2.69.1") == {
            "asn": None, "org": None, "network": "81.2.69.0/24"}
        assert ip_geo._place({"registered_country": {"iso_code": "DE", "names": {"en": "Germany"}}})["country"] == "Germany"

    @pytest.mark.parametrize("raw, shown", [
        ("Atria Convergence Technologies Pvt. Ltd.,", "Atria Convergence Technologies Pvt. Ltd."),
        ("  Example Transit Ltd ,; ", "Example Transit Ltd"),
        ("Cloudflare, Inc.", "Cloudflare, Inc."),
        (" ,", None),
        ("", None),
    ])
    def test_names_lose_stray_spaces_and_trailing_commas(self, raw, shown):
        assert ip_geo._network({"autonomous_system_organization": raw}, None, "81.2.69.1")["org"] == shown
        assert ip_geo._place({"city": {"names": {"en": raw}}})["city"] == shown
