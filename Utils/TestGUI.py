# GUI smoke test: starts the real main window on demo data and exercises it.
# Run from the project folder with:  python -m Utils.TestGUI
# (on Linux without a display:      xvfb-run -a python -m Utils.TestGUI)
#
# Dialogs are answered automatically and nothing outside a temporary folder is
# touched (your Conf/ is not read). A watchdog fails the test instead of hanging.
import os
import shutil
import sys
import tempfile
import traceback

import wx

from . import SnoopyWindowMgr as M
from .ConfigManager import ConfigManager
from .Credentials import Credentials
from .FakeSSHServer import FakeSSHServer
from .GUIManager import GUIManager
from .LocalHost import find_bash
from .RemoteFilesPanel import RemoteFileDialog
from .RemoteHost import script_command

TIMEOUT_MS = 120000


def main():
    work = tempfile.mkdtemp(prefix="snoopy-gui-test-")
    server = FakeSSHServer(address="127.0.0.2")
    os.makedirs(server.real_path("/opt/scripts"))
    with open(server.real_path("/opt/scripts/deploy.sh"), "w") as f:
        f.write("echo deploy\n")

    # Point Snoopy at a temporary configuration
    M.CONF_FILE = os.path.join(work, "Conf", "Config.ini")
    M.HOSTS_DIR = os.path.join(work, "Conf", "Hosts")
    M.HISTORY_FILE = os.path.join(work, "Conf", "History.ini")
    M.KNOWN_HOSTS_FILE = os.path.join(work, "known_hosts")
    M.LOG_DIR = os.path.join(work, "logs")
    local_script = os.path.join(work, "hello.sh")
    with open(local_script, "w", newline="\n") as f:
        f.write('echo "hello args=$*"\n')

    # Answer every dialog automatically
    answers, messages = [], []

    def ask(parent, title, fields, validate=None, intro=None):
        values = answers.pop(0)
        error = validate(values) if validate else None
        assert error is None, "%s rejected: %s" % (title, error)
        return values

    GUIManager.AskFields = staticmethod(ask)
    GUIManager.ConfirmGUI = staticmethod(lambda *a: True)
    GUIManager.PasswordGUI = staticmethod(lambda *a: server.password)
    wx.MessageBox = lambda message, *a, **k: messages.append(message)
    real_get, real_set = Credentials.get_password, Credentials.set_password
    Credentials.get_password = staticmethod(lambda *a: None)   # never touch the real keyring
    Credentials.set_password = staticmethod(lambda *a: None)

    app = wx.App(False)
    print("wxPython", wx.version())
    win = M.SnoopyWindowMgr(None, -1, "Snoopy", size=(1000, 650))
    win.Show()
    win.AskTrustHostKey = lambda *a: True
    failures = []

    def check(condition, label):
        print(("PASS " if condition else "FAIL ") + label)
        if not condition:
            failures.append(label)

    def enabled(key):
        event = wx.UpdateUIEvent(win.ids[key])
        event.SetEventObject(win)
        win.ProcessWindowEvent(event)
        return event.GetEnabled()

    def steps():
        yield 2000  # splash screen
        check(not win.hosts and any("No hosts" in m for m in messages), "starts with no hosts")

        # A host on this PC
        has_bash = find_bash()[0] is not None
        answers.append({"name": "Local", "hostname": "localhost", "username": "", "port": "22",
                        "password": "", "test": False})
        win.OnNewHost(None)
        local = win.hosts.get("Local")
        check(local is not None, "add localhost host")
        answers.append({"name": "Jobs"})
        win.OnCreateCategory(None)
        answers.append({"target": "Local / Jobs", "name": "hello", "script": local_script})
        win.OnNewTask(None)
        check(win.SelectedTask() == ("Local", "Jobs", "hello") and enabled("run"), "add local task")
        if has_bash:
            win.param_box.SetValue("one 'two three'")
            win.OnRunSelected(None)
            while local.job:
                yield 50
            output = local.console.output.GetValue()
            check("hello args=one two three" in output and "exit code 0" in output, "run local task in bash")
        else:
            print("SKIP run local task (no bash on this machine)")

        # A host over SSH (in-process test server)
        answers.append({"name": "Server", "hostname": "127.0.0.2", "username": server.username,
                        "port": str(server.port), "password": "", "test": True})
        win.OnNewHost(None)
        remote = win.hosts.get("Server")
        check(remote is not None and remote.IsConnected(), "add SSH host and connect")
        check(win.tree.IsBold(win.FindTreeItem("Server")), "connected host shown in bold")
        answers.append({"name": "Deploy"})
        win.OnCreateCategory(None)
        answers.append({"target": "Server / Deploy", "name": "deploy", "script": "/opt/scripts/deploy.sh"})
        win.OnNewTask(None)
        win.param_box.SetValue("--now")
        win.OnRunSelected(None)
        while remote.job:
            yield 50
        check(script_command("/opt/scripts/deploy.sh", ["--now"]) in server.commands, "run remote script in place")

        # The remote file picker and the Remote Files tab
        picker = RemoteFileDialog(win, remote.remote, "/opt/scripts/deploy.sh")
        picker.Show()
        yield 200
        check(picker.path == "/opt/scripts" and [e.filename for e in picker.entries] == ["deploy.sh"]
              and picker.GetPath() == "/opt/scripts/deploy.sh", "remote file picker")
        picker.Destroy()
        win.SelectTreeItem("Server")
        win.notebook.SetSelection(M.PAGE_FILES)
        yield 500
        remote.files.Navigate("/opt/scripts")
        check(any(e.filename == "deploy.sh" for e in remote.files.entries), "Remote Files tab")

        # Menus, help, search
        missing = [item.GetItemLabelText() for menu, _ in win.GetMenuBar().GetMenus()
                   for item in menu.GetMenuItems() if not item.IsSeparator() and not item.GetBitmapBundle().IsOk()]
        check(not missing, "every menu item has an icon %s" % (missing or ""))
        win.OnHelp(None)
        yield 200
        check(win.help_dialog.IsShown(), "help window")
        win.help_dialog.Hide()
        win.search.SetValue("deploy")
        yield 200
        check(win.FindTreeItem("Server", "Deploy", "deploy") is not None and win.FindTreeItem("Local") is None,
              "search filters the tree")
        win.search.SetValue("")
        win.Close()

    it = steps()

    def tick():
        try:
            wx.CallLater(next(it), tick)
        except StopIteration:
            pass
        except Exception:
            traceback.print_exc()
            failures.append("exception")
            win.Close()

    def watchdog():
        failures.append("timed out after %d s" % (TIMEOUT_MS // 1000))
        print("FAIL timed out")
        for window in wx.GetTopLevelWindows():
            window.Destroy()

    wx.CallLater(100, tick)
    wx.CallLater(TIMEOUT_MS, watchdog)
    app.MainLoop()

    Credentials.get_password, Credentials.set_password = real_get, real_set
    server.close()
    shutil.rmtree(server.root, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
    print("GUI smoke test:", "FAILED %s" % failures if failures else "OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
