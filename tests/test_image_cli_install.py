from pathlib import Path
from unittest.mock import patch
import subprocess

import pytest
from scripts.check_worker_clis import check_clis


def test_opencode_is_copied_out_of_root_before_worker_image_snapshot():
    dockerfile = Path("Dockerfile").read_text()
    install = "install -m 0755 /root/.opencode/bin/opencode /usr/local/bin/opencode"
    assert "OPENCODE_INSTALL_DIR=" not in dockerfile
    assert "bash /tmp/install-opencode.sh --no-modify-path" in dockerfile
    assert dockerfile.index(install) < dockerfile.index("&& opencode --version")
    assert dockerfile.index("RUN python /tmp/check_worker_clis.py") < dockerfile.index("FROM worker-base AS backend")


def test_smoke_check_drops_identity_and_does_not_inherit_secrets():
    with patch("os.geteuid", return_value=0), patch("os.chown"), \
         patch("shutil.which", side_effect=lambda name, **kw: "/usr/local/bin/" + name), \
         patch("subprocess.run") as run:
        check_clis()
    assert run.call_count == 3
    for call in run.call_args_list:
        assert call.kwargs["user"] == call.kwargs["group"] == 65534
        assert call.kwargs["extra_groups"] == ()
        assert set(call.kwargs["env"]) == {"PATH", "HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"}
        assert call.kwargs["check"] is True


def test_smoke_check_propagates_cli_failure():
    with patch("os.geteuid", return_value=0), patch("os.chown"), \
         patch("shutil.which", return_value="/usr/local/bin/opencode"), \
         patch("subprocess.run", side_effect=subprocess.CalledProcessError(127, "opencode")):
        with pytest.raises(subprocess.CalledProcessError):
            check_clis()
