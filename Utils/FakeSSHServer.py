# A tiny in-process SSH + SFTP server, used only by the tests.
#
# By default exec requests do not run anything. They record the command, print
# "ran: <command>" and "err" (on stderr), and exit with code 3. The command
# "hang" runs until the client closes the channel. SFTP is backed by a temp folder.
#
# With execute=True (POSIX only) commands really run in bash, with HOME set to the
# SFTP home folder ("/"), so tests can check real shell behaviour; pass root="/" to
# serve the real file system over SFTP as well.
import os
import posixpath
import shlex
import socket
import subprocess
import tempfile
import threading
import time

import paramiko


class _Handle(paramiko.SFTPHandle):
    def stat(self):
        try:
            return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def chattr(self, attr):
        return paramiko.SFTP_OK


class _SFTP(paramiko.SFTPServerInterface):
    ROOT = None

    def __init__(self, server, *args, **kwargs):
        super().__init__(server, *args, **kwargs)

    def canonicalize(self, path):
        if path in ("", "."):
            return "/"
        return posixpath.normpath(posixpath.join("/", path))

    def _real(self, path):
        return os.path.join(self.ROOT, *[p for p in self.canonicalize(path).split("/") if p])

    def list_folder(self, path):
        try:
            out = []
            for name in os.listdir(self._real(path)):
                attr = paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(self._real(path), name)))
                attr.filename = name
                out.append(attr)
            return out
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def stat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(os.stat(self._real(path)))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    lstat = stat

    def open(self, path, flags, attr):
        real = self._real(path)
        try:
            fd = os.open(real, flags | getattr(os, "O_BINARY", 0), 0o666)
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        if flags & os.O_WRONLY:
            mode = "ab" if flags & os.O_APPEND else "wb"
        elif flags & os.O_RDWR:
            mode = "a+b" if flags & os.O_APPEND else "r+b"
        else:
            mode = "rb"
        f = os.fdopen(fd, mode)
        handle = _Handle(flags)
        handle.filename = real
        handle.readfile = f
        handle.writefile = f
        return handle

    def remove(self, path):
        try:
            os.remove(self._real(path))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK

    def chattr(self, path, attr):
        return paramiko.SFTP_OK


class FakeSSHServer:

    def __init__(self, username="tester", password="secret", address="127.0.0.1", root=None, execute=False):
        self.username = username
        self.password = password
        self.commands = []
        self.execute = execute
        if root:
            self.root = root
        else:
            self.root = tempfile.mkdtemp(prefix="snoopy-fake-sftp-")
            os.makedirs(os.path.join(self.root, "tmp"))
        self.host_key = paramiko.RSAKey.generate(2048)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind((address, 0))
        self.address = address
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.transports = []
        threading.Thread(target=self._accept, daemon=True).start()

    def real_path(self, remote_path):
        return os.path.join(self.root, *[p for p in remote_path.split("/") if p])

    def _accept(self):
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            transport = paramiko.Transport(client)
            transport.add_server_key(self.host_key)
            sftp_cls = type("BoundSFTP", (_SFTP,), {"ROOT": self.root})
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, sftp_cls)
            self.transports.append(transport)
            try:
                transport.start_server(server=_Server(self))
            except (paramiko.SSHException, EOFError, OSError):
                pass

    def handle_exec(self, channel, command):
        # Let paramiko send the exec reply before we write output and close.
        time.sleep(0.1)
        self.commands.append(command)
        if command == "stdin":
            # Report whether the client closed our input (it should, so scripts never wait on it)
            deadline = time.monotonic() + 2
            while not channel.eof_received and time.monotonic() < deadline:
                time.sleep(0.05)
            closed = channel.eof_received
            channel.sendall(b"stdin closed\n" if closed else b"stdin open\n")
            channel.send_exit_status(0)
            channel.close()
            return
        if self.execute:
            self.run_in_bash(channel, command)
            return
        if command == "hang":
            while not channel.closed:
                channel.event.wait(0.05)
            return
        channel.sendall(("ran: %s\n" % command).encode())
        channel.sendall_stderr(b"err\n")
        channel.send_exit_status(3)
        channel.close()

    def run_in_bash(self, channel, command):
        """Really run the command; stdin stays open until the client sends EOF."""
        process = subprocess.Popen(["bash", "-c", command], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=dict(os.environ, HOME="/"))

        def pump(stream, send):
            for chunk in iter(lambda: stream.read1(4096), b""):
                try:
                    send(chunk)
                except (OSError, EOFError, paramiko.SSHException):
                    break

        readers = [threading.Thread(target=pump, args=(process.stdout, channel.sendall), daemon=True),
                   threading.Thread(target=pump, args=(process.stderr, channel.sendall_stderr), daemon=True)]
        for reader in readers:
            reader.start()
        while process.poll() is None and not channel.eof_received and not channel.closed:
            time.sleep(0.02)
        if channel.eof_received:
            process.stdin.close()
        while process.poll() is None and not channel.closed:
            time.sleep(0.02)
        if process.poll() is None:  # client stopped the command
            process.kill()
        for reader in readers:
            reader.join(2)
        if not channel.closed:
            channel.send_exit_status(process.wait())
            channel.close()

    def close(self):
        self.sock.close()
        for transport in self.transports:
            transport.close()


class _Server(paramiko.ServerInterface):
    def __init__(self, owner):
        self.owner = owner

    def get_allowed_auths(self, username):
        return "password"

    def check_auth_password(self, username, password):
        if (username, password) == (self.owner.username, self.owner.password):
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        threading.Thread(target=self.owner.handle_exec, args=(channel, command.decode()), daemon=True).start()
        return True
