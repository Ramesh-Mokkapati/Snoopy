"""Regenerate the README screenshots in docs/screenshots.

    .venv\\Scripts\\python.exe docs\\make_screenshots.py

Snoopy is started with made-up demo hosts (your own Conf/ is not read or changed).
An in-process fake SSH server stands in for "Production", and "Localhost" really
runs a small demo script through Git Bash. Only Snoopy's own windows are captured
(Win32 PrintWindow), never the rest of the screen. The window shows for a few
seconds while this runs. Windows only.
"""
import ctypes
import ctypes.wintypes as wt
import os
import shutil
import sys
import tempfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(APP, "docs", "screenshots")
sys.path.insert(0, APP)

# Per-monitor DPI aware, so window sizes from Win32 and DWM agree (and images are sharp)
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
import wx
import Utils.SnoopyWindowMgr as M
from Utils.FakeSSHServer import FakeSSHServer
from Utils.GUIManager import GUIManager, FieldsDialog
from Utils.RemoteFilesPanel import RemoteFileDialog
from Utils.ConfigManager import ConfigManager
from Utils.RemoteHost import RemoteHost

os.makedirs(OUT, exist_ok=True)
work = tempfile.mkdtemp(prefix="snoopy-shots-")

# ---------------------------------------------------------------- demo config
conf_dir = os.path.join(work, "Conf")
hosts_dir = os.path.join(conf_dir, "Hosts")
os.makedirs(hosts_dir)
REMOTE = "/opt/deploy/scripts"   # where the demo servers keep their scripts
LOCAL_DEMO = "C:/Tools"          # a local demo script that is shown as present
hosts = {
    "Production": ("10.0.0.1", "admin", {
        "Backup": [("backup_db", REMOTE + "/backup/backup_db.sh"), ("backup_files", REMOTE + "/backup/backup_files.sh")],
        "Cleanup": [("purge_old_logs", REMOTE + "/cleanup/purge_old_logs.sh")],
        "Reports": [("awr_report", REMOTE + "/reports/awr_report.sh"), ("disk_report", REMOTE + "/reports/disk_report.sh")],
    }),
    "Staging": ("10.0.0.2", "deploy", {
        "Deploy": [("deploy_app", REMOTE + "/deploy/deploy_app.sh"), ("rollback", REMOTE + "/deploy/rollback.sh")],
        "Checks": [("health_check", REMOTE + "/checks/health_check.sh")],
    }),
}
local_script = os.path.join(work, "pre_release_check.sh")
with open(local_script, "w", newline="\n") as f:
    f.write("""echo "Pre-release checks for version ${1:-1.4.2}"
echo
for step in "Git working tree clean" "Unit tests (214 passed)" "Config files valid" "Disk space on build drive: 61 GB free"; do
  sleep 0.3; printf '  %-42s OK\\n' "$step"
done
sleep 0.3
echo
echo "All checks passed - ready to deploy."
""")
hosts["Localhost"] = ("localhost", "", {
    "Tools": [("pre_release_check", local_script), ("list_videos", LOCAL_DEMO + "/list_videos.cmd")],
})

global_conf = ConfigManager(os.path.join(conf_dir, "Config.ini"))
for name, (address, user, categories) in hosts.items():
    path = os.path.join(hosts_dir, name + ".ini")
    host = ConfigManager(path)
    host.WriteToConfig("Host", "HostName", address)
    host.WriteToConfig("Host", "UserName", user)
    host.WriteToConfig("Host", "Port", "22")
    for category, tasks in categories.items():
        for task, script in tasks:
            host.WriteToConfig(category, task, script)
    global_conf.WriteToConfig("Hosts", name, "Hosts/%s.ini" % name)

demo_log = os.path.join(APP, "logs", "2026-10-25_09-12-40_Production_Backup_-_backup_db.log")
prod_history = ConfigManager(os.path.join(hosts_dir, "Production.history.ini"))
prod_history.WriteToConfig("Backup", "backup_db", "--full --compress")
prod_history.WriteToConfig("~LastRun", "Backup/backup_db", "2026-10-25 09:12|0|" + demo_log)

M.CONF_FILE = os.path.join(conf_dir, "Config.ini")
M.HOSTS_DIR = hosts_dir
M.HISTORY_FILE = os.path.join(conf_dir, "History.ini")
M.KNOWN_HOSTS_FILE = os.path.join(work, "known_hosts")
M.LOG_DIR = os.path.join(APP, "logs")  # shows a project path in the console; demo log removed afterwards

# The local demo script and demo log don't exist; show them as present.
real_isfile = os.path.isfile
os.path.isfile = lambda p: str(p).replace("\\", "/").startswith(LOCAL_DEMO) or p == demo_log or real_isfile(p)

# ----------------------------------------------- fake "Production" SSH server
server = FakeSSHServer(username="admin", password="demo", address="127.0.0.2")
for folder, files in {"var/log": [("syslog", 2_400_000), ("auth.log", 380_000), ("kern.log", 96_000),
                                  ("dpkg.log", 41_000), ("syslog.1", 5_100_000), ("syslog.2.gz", 610_000),
                                  ("auth.log.1.gz", 52_000)],
                      "var/log/nginx": [("access.log", 1_200_000)], "var/log/postgresql": [],
                      "var/log/apt": []}.items():
    os.makedirs(os.path.join(server.root, *folder.split("/")), exist_ok=True)
    for name, size in files:
        with open(os.path.join(server.root, *folder.split("/"), name), "wb") as f:
            f.write(b"x" * size)
# Production's scripts, which the task paths and the file picker show
for relative, size in [("backup/backup_db.sh", 2_140), ("backup/backup_files.sh", 1_630), ("backup/backup.conf", 310),
                       ("backup/restore_db.sh", 1_870), ("backup/README.txt", 920),
                       ("cleanup/purge_old_logs.sh", 780), ("reports/awr_report.sh", 1_210),
                       ("reports/disk_report.sh", 640), ("reports/tablespace_report.sh", 1_050)]:
    target = os.path.join(server.root, *(REMOTE.strip("/") + "/" + relative).split("/"))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as f:
        f.write(b"#" * size)
real_connect = RemoteHost.connect


def connect_to_fake(self, password, ask_trust, timeout=15):
    shown = (self.hostname, self.port)
    self.hostname, self.port = server.address, server.port
    try:
        real_connect(self, server.password, lambda *a: True, timeout)
    finally:
        self.hostname, self.port = shown


RemoteHost.connect = connect_to_fake
M.Credentials.get_password = staticmethod(lambda *a: "demo")
M.Credentials.set_password = staticmethod(lambda *a: None)
M.Credentials.delete_password = staticmethod(lambda *a: None)
wx.MessageBox = lambda *a, **k: None

# ------------------------------------------------------- window-only capture
user32, gdi32, dwmapi = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.dwmapi
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


for fn, args, res in [
        (user32.GetWindowDC, [wt.HWND], wt.HDC), (user32.ReleaseDC, [wt.HWND, wt.HDC], ctypes.c_int),
        (gdi32.CreateCompatibleDC, [wt.HDC], wt.HDC), (gdi32.CreateCompatibleBitmap, [wt.HDC, ctypes.c_int, ctypes.c_int], wt.HBITMAP),
        (gdi32.SelectObject, [wt.HDC, wt.HGDIOBJ], wt.HGDIOBJ), (gdi32.DeleteObject, [wt.HGDIOBJ], wt.BOOL),
        (gdi32.DeleteDC, [wt.HDC], wt.BOOL),
        (gdi32.GetDIBits, [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT], ctypes.c_int)]:
    fn.argtypes, fn.restype = args, res


def capture(window, name):
    """Render one top-level window with PrintWindow (no screen grab) and save it."""
    window.Update()
    hwnd = window.GetHandle()
    outer, frame = wt.RECT(), wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(outer))
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(frame), ctypes.sizeof(frame)) != 0:
        frame = outer  # DWMWA_EXTENDED_FRAME_BOUNDS unavailable
    width, height = outer.right - outer.left, outer.bottom - outer.top

    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    old = gdi32.SelectObject(memory_dc, bitmap)
    ok = user32.PrintWindow(hwnd, memory_dc, 2)  # PW_RENDERFULLCONTENT
    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = ctypes.create_string_buffer(width * height * 4)
    gdi32.SelectObject(memory_dc, old)
    rows = gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(header), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(hwnd, window_dc)
    if not ok or rows != height:
        raise RuntimeError("PrintWindow failed for %s (ok=%s rows=%s)" % (name, ok, rows))

    bgra = pixels.raw
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    image = wx.Image(width, height, bytes(rgb))
    # Drop the invisible resize border around Windows 10/11 windows
    image = image.GetSubImage(wx.Rect(frame.left - outer.left, frame.top - outer.top,
                                      frame.right - frame.left, frame.bottom - frame.top))
    image.SaveFile(os.path.join(OUT, name), wx.BITMAP_TYPE_PNG)
    print("saved", name, image.GetWidth(), "x", image.GetHeight(), os.path.getsize(os.path.join(OUT, name)), "bytes")


# ---------------------------------------------------------------- the shots
app = wx.App(False)
win = M.SnoopyWindowMgr(None, -1, "Snoopy - Beta", size=(1180, 720))
win.Show()
win.Center()
prod = win.hosts["Production"]
local = win.hosts["Localhost"]


def steps():
    yield 2500  # let the splash screen close
    # Remote Files on Production (connects to the fake server)
    win.SelectTreeItem("Production")
    win.notebook.SetSelection(M.PAGE_FILES)
    yield 600
    prod.files.Navigate("/var/log")
    yield 400
    capture(win, "remote-files.png")

    # Host details
    win.notebook.SetSelection(M.PAGE_TASK)
    win.SelectTreeItem("Production")
    yield 400
    capture(win, "host-details.png")

    # Task details (main window)
    win.SelectTreeItem("Production", "Backup", "backup_db")
    win.tree.SetFocus()
    yield 400
    capture(win, "main-window.png")

    # Console with a real local run
    win.SelectTreeItem("Localhost", "Tools", "pre_release_check")
    win.param_box.SetValue("1.4.2")
    win.OnRunSelected(None)
    while local.job:
        yield 100
    win.SelectTreeItem("Localhost")
    win.notebook.SetSelection(M.PAGE_CONSOLE)
    win.tree.SetFocus()
    yield 400
    capture(win, "console.png")

    # New Task dialog
    win.SelectTreeItem("Production", "Reports")
    targets = win.TaskTargets()
    choices = [h + M.CHOICE_SEPARATOR + c for h, c in targets]
    dialog = FieldsDialog(win, "New Task", [
        {"name": "target", "label": "Host / category:", "kind": "choice", "choices": choices,
         "value": "Production / Reports"},
        {"name": "name", "label": "Task name:", "value": "tablespace_report"},
        {"name": "script", "label": "Script on host:", "kind": "file", "value": REMOTE + "/reports/tablespace_report.sh"},
    ], intro="The script stays on the host and runs there. Browse... lists the host's files.")
    dialog.Show()
    dialog.controls["script"].SetInsertionPointEnd()
    dialog.controls["name"].SetFocus()
    dialog.controls["name"].SetInsertionPointEnd()
    yield 500
    capture(dialog, "new-task-dialog.png")
    dialog.Destroy()

    # Browsing the server for a task's script
    picker = RemoteFileDialog(win, prod.remote, REMOTE + "/backup/backup_db.sh",
                              "Choose a script on Production (%s)" % prod.address)
    picker.Show()
    names = [e.filename for e in picker.entries]
    picker.files.Select(names.index("backup_db.sh"))
    picker.files.Focus(names.index("backup_db.sh"))
    yield 500
    capture(picker, "remote-file-picker.png")
    picker.Destroy()
    win.Close()


it = steps()


def tick():
    try:
        wx.CallLater(next(it), tick)
    except StopIteration:
        pass
    except Exception:
        import traceback
        traceback.print_exc()
        win.Close()


wx.CallLater(100, tick)
app.MainLoop()
import glob
for log in glob.glob(os.path.join(APP, "logs", "*pre_release_check*.log")):
    os.remove(log)
server.close()
shutil.rmtree(server.root, ignore_errors=True)
shutil.rmtree(work, ignore_errors=True)
print("done")
