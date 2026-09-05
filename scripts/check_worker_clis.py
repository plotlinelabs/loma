"""Build-time CLI startup check using an unprivileged identity and clean HOME."""
import os
import shutil
import subprocess
import tempfile

CLIS = ("opencode", "claude", "codex")


def check_clis():
    if os.geteuid() != 0:
        raise RuntimeError("Run this image-build check as root so privilege dropping is tested")
    with tempfile.TemporaryDirectory(prefix="loma-cli-smoke-") as home:
        os.chown(home, 65534, 65534)
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home,
               "XDG_CONFIG_HOME": home + "/config", "XDG_DATA_HOME": home + "/data"}
        for cli in CLIS:
            binary = shutil.which(cli, path=env["PATH"])
            if not binary:
                raise RuntimeError(f"Missing CLI on worker PATH: {cli}")
            subprocess.run([binary, "--version"], env=env, cwd=home,
                           user=65534, group=65534, extra_groups=(),
                           check=True, timeout=60)


if __name__ == "__main__":
    check_clis()
