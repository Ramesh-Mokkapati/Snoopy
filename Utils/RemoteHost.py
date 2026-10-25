import base64
import codecs
import hashlib
import os
import posixpath
import re
import shlex
import stat
import threading

import paramiko

from .Paths import KNOWN_HOSTS_FILE


class HostKeyRejected(paramiko.SSHException):
    """The user declined to trust an unknown host key."""


def fingerprint(key):
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


class _AskUserPolicy(paramiko.MissingHostKeyPolicy):
    """Ask before trusting a host key we have not seen; remember the answer."""

    def __init__(self, ask, known_hosts_file):
        self.ask = ask
        self.known_hosts_file = known_hosts_file

    def missing_host_key(self, client, hostname, key):
        if not self.ask(hostname, key.get_name(), fingerprint(key)):
            raise HostKeyRejected("Host key for %s was not trusted" % hostname)
        client.get_host_keys().add(hostname, key.get_name(), key)
        if self.known_hosts_file:
            os.makedirs(os.path.dirname(self.known_hosts_file), exist_ok=True)
            client.save_host_keys(self.known_hosts_file)


class RemoteHost:
    """An SSH connection to the configured host, for running commands and SFTP."""

    def __init__(self, hostname, username, port=22, known_hosts_file=KNOWN_HOSTS_FILE):
        self.hostname = hostname
        self.username = username
        self.port = port
        self.known_hosts_file = known_hosts_file
        self.client = None
        self._lock = threading.Lock()
        self._channels = set()
        self._cancelled = set()

    @property
    def key(self):
        return (self.hostname, self.username, self.port)

    def connect(self, password, ask_trust, timeout=15):
        """Connect with a password. ask_trust(host, key_type, fingerprint) -> bool
        is called for an unknown host key. Raises paramiko exceptions or OSError."""
        client = paramiko.SSHClient()
        try:
            client.load_system_host_keys()
        except OSError:
            pass
        if self.known_hosts_file and os.path.isfile(self.known_hosts_file):
            client.load_host_keys(self.known_hosts_file)
        client.set_missing_host_key_policy(_AskUserPolicy(ask_trust, self.known_hosts_file))
        try:
            client.connect(self.hostname, self.port, self.username, password,
                           timeout=timeout, allow_agent=False, look_for_keys=False)
        except BaseException:
            client.close()
            raise
        self.client = client

    def is_connected(self):
        transport = self.client.get_transport() if self.client else None
        return bool(transport and transport.is_active())

    def close(self):
        self.cancel()
        if self.client:
            self.client.close()
            self.client = None

    def _transport(self):
        if not self.is_connected():
            raise paramiko.SSHException("Not connected to %s" % self.hostname)
        return self.client.get_transport()

    def open_sftp(self):
        return paramiko.SFTPClient.from_transport(self._transport())

    def run(self, command, on_output=None):
        """Run a command on the host, streaming stdout+stderr to on_output(text).

        Returns the exit code, or -1 if it was stopped with cancel().
        """
        channel = self._transport().open_session()
        with self._lock:
            self._channels.add(channel)
        try:
            channel.set_combine_stderr(True)
            channel.exec_command(command)
            channel.shutdown_write()  # no input: a script that reads stdin gets end-of-input, not a hang
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while True:
                try:
                    data = channel.recv(32768)
                except (OSError, EOFError, paramiko.SSHException):
                    data = b""
                text = decoder.decode(data, final=not data)
                if text and on_output:
                    on_output(text)
                if not data:
                    break
            with self._lock:
                if channel in self._cancelled:
                    return -1
            return channel.recv_exit_status()
        finally:
            with self._lock:
                self._channels.discard(channel)
                self._cancelled.discard(channel)
            channel.close()

    def cancel(self):
        """Stop every command started with run()."""
        with self._lock:
            channels = list(self._channels)
            self._cancelled.update(channels)
        for channel in channels:
            channel.close()

    def resolve_path(self, sftp, path):
        """Turn a script path as stored in the config (absolute, or ~/...) into an absolute path."""
        if path == "~" or path.startswith("~/"):
            return posixpath.join(sftp.normalize("."), path[2:])
        return path

    def script_exists(self, path):
        """True if the path is an existing regular file on the host."""
        sftp = self.open_sftp()
        try:
            return stat.S_ISREG(sftp.stat(self.resolve_path(sftp, path)).st_mode or 0)
        except (OSError, paramiko.SSHException):
            return False
        finally:
            sftp.close()

    def run_script(self, remote_path, args, on_output=None):
        """Run a script that lives on the host, in place, with the given arguments.

        Raises FileNotFoundError if it isn't there. See script_command for how it is started.
        """
        if not self.script_exists(remote_path):
            raise FileNotFoundError("Script not found on %s: %s" % (self.hostname, remote_path))
        return self.run(script_command(remote_path, args), on_output)


def remote_quote(path):
    """Shell-quote a remote path, keeping a leading ~/ so it still means the user's home."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def script_command(path, args):
    """The command that runs a script stored on the host.

    It starts in the script's own folder (so files next to it are found). An executable
    script runs directly, so its #! line picks the interpreter; otherwise bash runs it.
    """
    folder = posixpath.dirname(path) or "."
    script = remote_quote(path)
    arguments = "".join(" " + shlex.quote(a) for a in args)
    return "cd -- %s && if [ -x %s ]; then exec %s%s; else exec bash %s%s; fi" % (
        remote_quote(folder), script, script, arguments, script, arguments)


def split_args(parameters):
    """Split a parameter string the way a POSIX shell would. Raises ValueError."""
    return shlex.split(parameters or "")


_GLOB_SAFE = re.compile(r"^[A-Za-z0-9_./*?\[\]~+,:=@%-]+$")


def quote_path(path):
    """Quote a remote path, leaving simple globs (e.g. /var/log/*.gz) unquoted
    so the remote shell expands them."""
    if any(c in path for c in "*?[") and _GLOB_SAFE.match(path):
        return path
    return shlex.quote(path)


def build_grep(pattern, path, ignore_case=False, recursive=True):
    options = "-n" + ("r" if recursive else "") + ("i" if ignore_case else "")
    return "grep %s -e %s -- %s" % (options, shlex.quote(pattern), quote_path(path))


def build_zgrep(pattern, path, ignore_case=False):
    options = "-n" + ("i" if ignore_case else "")
    return "zgrep %s -e %s -- %s" % (options, shlex.quote(pattern), quote_path(path))


def build_du(path, per_folder=True):
    if per_folder:
        return "du -h --max-depth=1 -- %s | sort -h" % quote_path(path)
    return "du -sh -- %s" % quote_path(path)
