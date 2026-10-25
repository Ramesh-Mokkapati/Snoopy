"""Running tasks on this PC instead of over SSH, for hosts at localhost / 127.0.0.1.

LocalHost has the same interface as RemoteHost (run, run_script, cancel, open_sftp, ...)
so the rest of Snoopy does not care where a command runs. Commands and .sh scripts run
in bash: Git Bash on Windows if installed, otherwise WSL's bash, otherwise bash on the
PATH. Windows scripts run with their own interpreter: .cmd/.bat with cmd.exe and .ps1
with PowerShell.
"""
import codecs
import os
import queue
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time

import paramiko

LOCAL_NAMES = {"localhost", "127.0.0.1", "::1"}
WINDOWS_SCRIPTS = {".cmd", ".bat", ".ps1"}


def is_windows_script(path):
    return os.path.splitext(path)[1].lower() in WINDOWS_SCRIPTS


def console_encoding():
    """The encoding cmd.exe and PowerShell use when their output is piped (the OEM code page)."""
    if os.name == "nt":
        try:
            import ctypes
            encoding = "cp%d" % ctypes.windll.kernel32.GetOEMCP()
            codecs.lookup(encoding)
            return encoding
        except (AttributeError, OSError, LookupError):
            pass
    return "utf-8"


def cmd_quote(arg):
    """Quote an argument for cmd.exe so & | < > ^ are taken literally."""
    return '"%s"' % arg.replace('"', '""')


def is_local(hostname):
    return (hostname or "").strip().lower() in LOCAL_NAMES


def find_bash():
    """Return (bash path, flavor) where flavor is 'git', 'wsl' or 'posix'; or (None, None)."""
    if os.name != "nt":
        path = shutil.which("bash")
        return (path, "posix") if path else (None, None)
    candidates = []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramW6432"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            candidates.append(os.path.join(base, "Git", "bin", "bash.exe"))
    for path in candidates:
        if os.path.isfile(path):
            return path, "git"
    on_path = shutil.which("bash")
    if on_path:
        system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32").lower()
        return on_path, "wsl" if os.path.dirname(on_path).lower() == system32 else "git"
    return None, None


def to_bash_path(path, flavor):
    """Convert a Windows path to one bash understands."""
    if flavor == "wsl":
        drive, rest = os.path.splitdrive(os.path.abspath(path))
        return "/mnt/%s%s" % (drive.rstrip(":").lower(), rest.replace("\\", "/"))
    if flavor == "git":
        return os.path.abspath(path).replace("\\", "/")
    return path


class LocalFiles:
    """The subset of paramiko's SFTPClient the Remote Files tab uses, for this PC's disk.
    Paths use forward slashes (C:/Users/me on Windows)."""

    @staticmethod
    def _path(path):
        path = path or "/"
        if re.match(r"^[A-Za-z]:$", path):
            path += "/"  # "C:" alone would mean the current folder on drive C
        return os.path.abspath(os.path.expanduser(path))

    def normalize(self, path):
        if path in ("", "."):
            path = os.path.expanduser("~")
        return self._path(path).replace("\\", "/")

    def listdir_attr(self, path):
        folder = self._path(path)
        entries = []
        for name in os.listdir(folder):
            try:
                attr = paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(folder, name)))
            except OSError:
                attr = paramiko.SFTPAttributes()
                attr.st_mode, attr.st_size, attr.st_mtime = stat.S_IFREG, 0, 0
            attr.filename = name
            entries.append(attr)
        return entries

    def stat(self, path):
        return paramiko.SFTPAttributes.from_stat(os.stat(self._path(path)))

    def _copy(self, source, target, callback):
        shutil.copyfile(self._path(source), self._path(target))
        if callback:
            size = os.path.getsize(self._path(target))
            callback(size, size)

    def get(self, remotepath, localpath, callback=None):
        self._copy(remotepath, localpath, callback)

    def put(self, localpath, remotepath, callback=None):
        self._copy(localpath, remotepath, callback)

    def close(self):
        pass


class LocalHost:
    """Runs commands and scripts on this PC through bash."""

    def __init__(self, hostname="localhost", username="", port=22):
        self.hostname = hostname
        self.username = username
        self.port = port
        self.bash, self.flavor = None, None
        self._lock = threading.Lock()
        self._processes = set()
        self._cancelled = set()

    @property
    def key(self):
        return (self.hostname, self.username, self.port)

    def connect(self):
        """Find bash. On Windows, .cmd/.bat/.ps1 tasks still work without it;
        elsewhere bash is required and OSError is raised if it is missing."""
        self.bash, self.flavor = find_bash()
        if not self.bash and os.name != "nt":
            raise OSError("bash was not found on this PC.")
        self.ready = True

    def is_connected(self):
        return getattr(self, "ready", False)

    @property
    def description(self):
        if self.bash:
            return "bash: " + self.bash
        return "cmd/PowerShell only - bash not found (install Git for Windows for .sh scripts)"

    def close(self):
        self.cancel()
        self.ready = False

    def _need_bash(self):
        if not self.bash:
            raise OSError("bash was not found. Install Git for Windows (Git Bash) or WSL to run "
                          "shell commands and .sh scripts on this PC.")

    def open_sftp(self):
        return LocalFiles()

    def _popen(self, command, cwd=None):
        options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "cwd": cwd}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        return subprocess.Popen(command, **options)

    def _run_process(self, command, on_output, encoding="utf-8", cwd=None):
        if not self.is_connected():
            raise OSError("Not ready to run on this PC")
        process = self._popen(command, cwd)
        with self._lock:
            self._processes.add(process)

        # Read on a helper thread: a stopped command's leftover child processes can keep
        # the pipe open, and we must not wait for them.
        chunks = queue.Queue()

        def reader():
            try:
                while True:
                    data = process.stdout.read1(32768)
                    chunks.put(data)
                    if not data:
                        break
            except (OSError, ValueError):
                chunks.put(b"")

        threading.Thread(target=reader, daemon=True).start()
        decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        exited_at = None
        try:
            while True:
                with self._lock:
                    if process in self._cancelled:
                        return -1
                try:
                    data = chunks.get(timeout=0.1)
                except queue.Empty:
                    if process.poll() is None:
                        continue
                    # Exited: give background children a moment to finish writing, then stop.
                    exited_at = exited_at or time.monotonic()
                    if time.monotonic() - exited_at < 2:
                        continue
                    data = b""
                text = decoder.decode(data, final=not data)
                if text and on_output:
                    on_output(text)
                if not data:
                    break
            code = process.wait()
            with self._lock:  # a stopped process can end before the loop notices
                if process in self._cancelled:
                    return -1
            return code
        finally:
            with self._lock:
                self._processes.discard(process)
                self._cancelled.discard(process)

    def run(self, command, on_output=None):
        """Run a bash command line. Returns the exit code, or -1 if stopped with cancel()."""
        self._need_bash()
        return self._run_process([self.bash, "-c", command], on_output)

    def run_script(self, local_path, args, on_output=None):
        """Run a script with the given arguments.

        Every script starts in its own folder (as if double-clicked). .cmd/.bat run with
        cmd.exe and .ps1 with PowerShell. Anything else runs with bash, in place - or from a
        temporary copy with Windows line endings converted, if it has any.
        """
        folder = os.path.dirname(os.path.abspath(local_path))
        extension = os.path.splitext(local_path)[1].lower()
        if os.name == "nt" and extension in (".cmd", ".bat"):
            script = os.path.abspath(local_path)
            inner = " ".join([cmd_quote(script)] + [cmd_quote(a) for a in args])
            # /d: skip AutoRun, /s /c "...": run the inner line exactly as written
            return self._run_process('cmd.exe /d /s /c "%s"' % inner, on_output,
                                     console_encoding(), os.path.dirname(script))
        if os.name == "nt" and extension == ".ps1":
            script = os.path.abspath(local_path)
            return self._run_process(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                                      "Bypass", "-File", script] + list(args), on_output,
                                     console_encoding(), os.path.dirname(script))

        self._need_bash()
        with open(local_path, "rb") as script:
            data = script.read()
        if b"\r\n" not in data:
            return self._run_process([self.bash, to_bash_path(local_path, self.flavor)] + list(args),
                                     on_output, cwd=folder)
        handle, temp_path = tempfile.mkstemp(prefix="snoopy-", suffix="-" + os.path.basename(local_path))
        try:
            with os.fdopen(handle, "wb") as temp:
                temp.write(data.replace(b"\r\n", b"\n"))
            return self._run_process([self.bash, to_bash_path(temp_path, self.flavor)] + list(args),
                                     on_output, cwd=folder)
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def cancel(self):
        """Stop every running command, including anything it started."""
        with self._lock:
            processes = list(self._processes)
            self._cancelled.update(processes)
        for process in processes:
            if process.poll() is not None:
                continue
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                process.kill()
