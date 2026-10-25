import os
import shutil
import tempfile
import threading
import time

import paramiko

from .FakeSSHServer import FakeSSHServer
from .RemoteHost import RemoteHost, HostKeyRejected, build_grep, build_zgrep, build_du, quote_path, split_args, \
    script_command, remote_quote


def TestCommandBuilders():
    assert build_grep("a'b", "/var/log", ignore_case=True) == "grep -nri -e 'a'\"'\"'b' -- /var/log"
    assert build_grep("x", "/my dir", recursive=False) == "grep -n -e x -- '/my dir'"
    assert build_zgrep("ERROR", "/var/log/*.gz") == "zgrep -n -e ERROR -- /var/log/*.gz"
    assert quote_path("/var/log/*.gz; rm -rf /") == "'/var/log/*.gz; rm -rf /'"
    assert build_du("/opt") == "du -h --max-depth=1 -- /opt | sort -h"
    assert build_du("/opt", per_folder=False) == "du -sh -- /opt"
    assert split_args('-v "two words" x') == ["-v", "two words", "x"]
    try:
        split_args('"unclosed')
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert remote_quote("/opt/a b.sh") == "'/opt/a b.sh'"
    assert remote_quote("~/x y.sh") == "\"$HOME\"/'x y.sh'"
    assert script_command("/opt/s/run.sh", ["-v", "two words"]) == (
        "cd -- /opt/s && if [ -x /opt/s/run.sh ]; then exec /opt/s/run.sh -v 'two words'; "
        "else exec bash /opt/s/run.sh -v 'two words'; fi")
    print("Command builders: OK")


def TestRemoteHost():
    server = FakeSSHServer()
    tmpdir = tempfile.mkdtemp()
    known_hosts = os.path.join(tmpdir, "user", "known_hosts")
    try:
        def host():
            return RemoteHost("127.0.0.1", server.username, server.port, known_hosts_file=known_hosts)

        # Unknown host key, user says no
        remote = host()
        try:
            remote.connect(server.password, lambda *a: False)
            raise AssertionError("expected HostKeyRejected")
        except HostKeyRejected:
            pass
        assert not os.path.exists(known_hosts)

        # Unknown host key, user says yes: remembered for next time
        asked = []
        remote = host()
        remote.connect(server.password, lambda h, t, fp: asked.append(fp) or True)
        assert asked and asked[0].startswith("SHA256:") and os.path.isfile(known_hosts)
        remote.close()
        remote = host()
        remote.connect(server.password, lambda *a: (_ for _ in ()).throw(AssertionError("asked again")))

        # Wrong password
        try:
            host().connect("wrong", lambda *a: True)
            raise AssertionError("expected AuthenticationException")
        except paramiko.AuthenticationException:
            pass

        # Run a command: stdout and stderr both stream back, exit code is returned
        chunks = []
        code = remote.run("echo hi", chunks.append)
        output = "".join(chunks)
        assert code == 3 and "ran: echo hi" in output and "err" in output, (code, output)

        # Commands get no input, so a script that reads stdin can't hang waiting for it
        chunks = []
        assert remote.run("stdin", chunks.append) == 0 and "".join(chunks) == "stdin closed\n", chunks

        # Run a script stored on the host, in place: from its folder, arguments quoted, nothing uploaded
        os.makedirs(server.real_path("/opt/my scripts"))
        with open(server.real_path("/opt/my scripts/backup.sh"), "w") as f:
            f.write("echo backup\n")
        before = set(os.listdir(server.real_path("/tmp")))
        assert remote.script_exists("/opt/my scripts/backup.sh")
        assert not remote.script_exists("/opt/my scripts")  # a folder is not a script
        code = remote.run_script("/opt/my scripts/backup.sh", ["a b", "c;d"])
        assert code == 3 and server.commands[-1] == script_command("/opt/my scripts/backup.sh", ["a b", "c;d"])
        assert server.commands[-1].startswith("cd -- '/opt/my scripts' && ")
        assert set(os.listdir(server.real_path("/tmp"))) == before  # nothing copied to the host
        try:
            remote.run_script("/opt/missing.sh", [])
            raise AssertionError("expected FileNotFoundError")
        except FileNotFoundError as error:
            assert "/opt/missing.sh" in str(error)
        os.makedirs(server.real_path("/bin"), exist_ok=True)
        with open(server.real_path("/bin/home.sh"), "w") as f:  # the fake server's home folder is /
            f.write("echo home\n")
        assert remote.script_exists("~/bin/home.sh")

        # Stop a running command
        threading.Timer(0.5, remote.cancel).start()
        assert remote.run("hang") == -1

        # SFTP listing used by the Remote Explorer
        sftp = remote.open_sftp()
        sftp.putfo(__import__("io").BytesIO(b"x"), "/tmp/listed.txt")
        assert "listed.txt" in [a.filename for a in sftp.listdir_attr("/tmp")]
        sftp.close()
        remote.close()
        assert not remote.is_connected()
        print("RemoteHost: OK")
    finally:
        server.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
        shutil.rmtree(server.root, ignore_errors=True)


def TestLocalHost():
    from .LocalHost import LocalHost, LocalFiles, is_local, find_bash
    assert is_local("localhost") and is_local(" 127.0.0.1 ") and is_local("LOCALHOST") and not is_local("10.0.0.1")
    if find_bash()[0] is None:
        print("LocalHost: SKIPPED (no bash on this PC)")
        return
    host = LocalHost()
    host.connect()
    chunks = []
    assert host.run("echo hi; echo err >&2; exit 4", chunks.append) == 4
    assert "".join(chunks).split() == ["hi", "err"], chunks

    tmpdir = tempfile.mkdtemp()
    try:
        script = os.path.join(tmpdir, "my script.sh")
        with open(script, "wb") as f:
            f.write(b'echo "count:$#"\r\nfor a in "$@"; do echo "[$a]"; done\r\n')
        chunks = []
        assert host.run_script(script, ["a b", "c;d"], chunks.append) == 0
        assert "".join(chunks).split("\n")[:3] == ["count:2", "[a b]", "[c;d]"], chunks

        threading.Timer(0.5, host.cancel).start()
        start = time.monotonic()
        chunks = []
        assert host.run("echo start; sleep 20; echo never", chunks.append) == -1
        assert time.monotonic() - start < 10 and "never" not in "".join(chunks)

        files = LocalFiles()
        folder = files.normalize(tmpdir)
        assert "my script.sh" in [a.filename for a in files.listdir_attr(folder)]
        files.get(folder + "/my script.sh", os.path.join(tmpdir, "copy.sh"))
        assert os.path.isfile(os.path.join(tmpdir, "copy.sh"))
        if os.name == "nt":
            assert files.normalize("C:") == "C:/"
            # Windows scripts run with cmd.exe / PowerShell from their own folder, not bash
            batch = os.path.join(tmpdir, "args test.cmd")
            with open(batch, "w") as f:
                f.write('@echo off\r\ncd\r\necho 1=%1\r\necho 2=%2\r\npause\r\nexit /b 3\r\n')
            chunks = []
            assert host.run_script(batch, ["a & b", "x|y"], chunks.append) == 3
            lines = "".join(chunks).splitlines()
            assert lines[:3] == [tmpdir, '1="a & b"', '2="x|y"'], lines
            ps1 = os.path.join(tmpdir, "t.ps1")
            with open(ps1, "w") as f:
                f.write('Write-Output "args: $($args -join \',\')"; exit 5\r\n')
            chunks = []
            assert host.run_script(ps1, ["one", "two three"], chunks.append) == 5
            assert "".join(chunks).strip() == "args: one,two three", chunks
        host.close()
        assert not host.is_connected()
        print("LocalHost: OK")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def TestRemoteScriptsWithRealBash():
    """Run scripts stored on a 'remote' host through a real bash (POSIX only)."""
    if os.name == "nt" or not shutil.which("bash"):
        print("Remote scripts with real bash: SKIPPED (needs Linux/macOS)")
        return
    work = tempfile.mkdtemp(prefix="snoopy-remote-")
    server = FakeSSHServer(root="/", execute=True)
    try:
        folder = os.path.join(work, "my scripts")
        os.makedirs(folder)
        with open(os.path.join(folder, "settings.conf"), "w") as f:
            f.write("ENV=prod\n")
        with open(os.path.join(folder, "plain.sh"), "w") as f:  # not executable -> run with bash
            f.write('source ./settings.conf\necho "env=$ENV pwd=$(pwd) args=$# [$1] [$2]"\n'
                    'read -r answer || echo "stdin: end of input"\nexit 4\n')
        with open(os.path.join(folder, "tool.py"), "w") as f:  # executable -> its #! line is used
            f.write('#!/usr/bin/env python3\nimport sys\nprint("python args", sys.argv[1:])\n')
        os.chmod(os.path.join(folder, "tool.py"), 0o755)

        remote = RemoteHost("127.0.0.1", server.username, server.port,
                            known_hosts_file=os.path.join(work, "known_hosts"))
        remote.connect(server.password, lambda *a: True)
        chunks = []
        code = remote.run_script(os.path.join(folder, "plain.sh"), ["a b", "$(id)"], chunks.append)
        output = "".join(chunks)
        assert code == 4, (code, output)
        assert "env=prod pwd=%s args=2 [a b] [$(id)]" % folder in output, output   # runs in its folder, args literal
        assert "stdin: end of input" in output, output                              # read doesn't hang
        chunks = []
        assert remote.run_script(os.path.join(folder, "tool.py"), ["x"], chunks.append) == 0
        assert "python args ['x']" in "".join(chunks), chunks
        os.makedirs("/bin", exist_ok=True)  # HOME is "/" on this test server, so ~/... means /...
        relative = os.path.relpath(os.path.join(folder, "plain.sh"), "/")
        chunks = []
        assert remote.run_script("~/" + relative, ["home"], chunks.append) == 4, chunks
        assert "[home]" in "".join(chunks)
        remote.close()
        print("Remote scripts with real bash: OK")
    finally:
        server.close()
        shutil.rmtree(work, ignore_errors=True)
