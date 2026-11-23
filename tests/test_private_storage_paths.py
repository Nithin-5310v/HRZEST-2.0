"""utils/storage.py: open_private() and delete_private() only touch files inside
private_uploads/. A stored_ref is read back from a database column, so a row
naming a file outside the folder -- by "..", an absolute or relative path, or a
link -- is refused like a missing file, and the download routes say "missing".
"""
import io
import os

import pytest
from werkzeug.datastructures import FileStorage

from extensions import app as flask_app
from tests.test_onboarding import _employee_session, assigned_onboarding, template  # noqa: F401 -- fixtures
from utils import storage
from utils.storage import PrivatePathError, delete_private, open_private, save_private

PDF = b"%PDF-1.4 inside"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_APP_ROOT", str(tmp_path))
    (tmp_path / "private_uploads").mkdir()
    return tmp_path


@pytest.fixture
def secret(root):
    """A readable file next to private_uploads/, not in it."""
    p = root / "secret.txt"
    p.write_bytes(b"not an upload")
    return p


def _save(root, rel="docs/a.pdf"):
    ref, err = save_private(str(root), FileStorage(stream=io.BytesIO(PDF), filename="a.pdf"), rel)
    assert err is None
    return ref


class TestInside:
    def test_a_saved_file_reads_back_and_deletes(self, root):
        ref = _save(root)
        assert open_private(ref) == PDF
        assert delete_private(ref) is True and not os.path.exists(ref)

    def test_dot_dot_that_stays_inside_is_fine(self, root):
        _save(root)
        assert open_private(os.path.join(str(root), "private_uploads", "docs", "..", "docs", "a.pdf")) == PDF


class TestOutside:
    def test_dot_dot_out_of_the_folder(self, root, secret):
        with pytest.raises(PrivatePathError):
            open_private(os.path.join(str(root), "private_uploads", "..", "secret.txt"))

    def test_an_absolute_path_elsewhere(self, secret):
        with pytest.raises(PrivatePathError):
            open_private(str(secret))

    def test_a_relative_path(self, secret, monkeypatch):
        monkeypatch.chdir(secret.parent)
        with pytest.raises(PrivatePathError):
            open_private("secret.txt")

    def test_a_folder_that_only_starts_with_the_same_name(self, root):
        twin = root / "private_uploads_old"
        twin.mkdir()
        (twin / "x.pdf").write_bytes(PDF)
        with pytest.raises(PrivatePathError):
            open_private(str(twin / "x.pdf"))

    def test_the_folder_itself(self, root):
        with pytest.raises(PrivatePathError):
            open_private(str(root / "private_uploads"))

    @pytest.mark.parametrize("ref", ["", None, 42, "a\0b"])
    def test_junk(self, root, ref):
        with pytest.raises(PrivatePathError):
            open_private(ref)

    def test_a_link_inside_pointing_out(self, root, secret):
        link = root / "private_uploads" / "link.txt"
        try:
            os.symlink(secret, link)
        except (OSError, NotImplementedError):
            pytest.skip("this system does not allow creating symlinks")
        with pytest.raises(PrivatePathError):
            open_private(str(link))

    def test_delete_refuses_and_leaves_the_file(self, secret):
        with pytest.raises(PrivatePathError):
            delete_private(str(secret))
        assert secret.exists()

    def test_callers_see_a_missing_file(self):
        assert issubclass(PrivatePathError, FileNotFoundError)

    def test_a_refusal_is_recorded_as_a_security_event(self, secret, monkeypatch):
        import extensions
        events = []
        monkeypatch.setattr(extensions, "log_security_event", lambda et, msg, **kw: events.append((et, kw)))
        with pytest.raises(PrivatePathError):
            open_private(str(secret))
        assert [et for et, _kw in events] == ["access.private_file_refused"]
        assert "secret.txt" in events[0][1]["stored_ref"] and events[0][1]["level"] == "WARNING"


def test_every_writer_saves_under_the_root_that_is_checked():
    """save_private() is given app.root_path (and qr_generator.py's own folder);
    open_private() checks <_APP_ROOT>/private_uploads. They must be one place."""
    import qr_generator
    real = os.path.realpath
    assert real(flask_app.root_path) == real(storage._APP_ROOT)
    assert real(os.path.dirname(os.path.abspath(qr_generator.__file__))) == real(storage._APP_ROOT)


def test_a_download_whose_row_names_a_file_outside_says_missing(client, db_engine, seed_employee, assigned_onboarding):  # noqa: F811
    """End to end: an onboarding task row pointing at the app's own source is
    never sent; the employee is told the document is missing."""
    outside = os.path.join(flask_app.root_path, "app.py")
    assert os.path.isfile(outside)
    _ob_id, task_row_ids = assigned_onboarding
    cur = db_engine.cursor()
    cur.execute("UPDATE employee_onboarding_tasks SET document_path=%s WHERE id=%s", (outside, task_row_ids[0]))
    cur.close()
    _employee_session(client, seed_employee)
    resp = client.get(f"/onboarding_document/{task_row_ids[0]}", follow_redirects=False)
    assert resp.status_code == 302 and "/my_onboarding" in resp.headers["Location"]
    with client.session_transaction() as sess:
        assert ("danger", "Document file is missing or unreadable.") in sess.get("_flashes", [])
