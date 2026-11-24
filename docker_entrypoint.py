"""Ensure /app/data is writable by appuser, then drop privileges and exec CMD."""

import os
from pathlib import Path
import pwd
import sys


def main():
    pw = pwd.getpwnam("appuser")
    data_dir = Path(os.environ.get("DATA_DIR", "/app/data"))

    if os.geteuid() == 0:
        data_dir.mkdir(parents=True, exist_ok=True)
        targets = [data_dir, *data_dir.rglob("*")]
        for path in targets:
            try:
                os.chown(path, pw.pw_uid, pw.pw_gid)
            except OSError:
                pass
            # Fallback for host bind mounts (e.g. Docker Desktop NTFS) where chown is ignored
            try:
                if path.is_dir():
                    os.chmod(path, 0o777)
                else:
                    os.chmod(path, 0o666)
            except OSError:
                pass

        os.setgroups([])
        os.setgid(pw.pw_gid)
        os.setuid(pw.pw_uid)
        os.environ["HOME"] = pw.pw_dir
        os.environ["USER"] = pw.pw_name

    if len(sys.argv) < 2:
        raise SystemExit("No command provided to docker_entrypoint.py")

    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
