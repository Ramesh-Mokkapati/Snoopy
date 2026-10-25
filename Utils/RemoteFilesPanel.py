import os
import posixpath
import stat
import threading
from time import localtime, strftime

import paramiko
import wx

from .GUIManager import GUIManager, art, ART_REFRESH

_WINDOWS_UNSAFE = '<>:"/\\|?*'


def _local_name(name):
    return "".join("_" if c in _WINDOWS_UNSAFE else c for c in name) or "_"


def _size(num):
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return ("%d %s" % (num, unit)) if unit == "B" else ("%.1f %s" % (num, unit))
        num /= 1024.0


class RemoteFilesPanel(wx.Panel):
    """Browse the host's file system over SFTP, with upload and download.

    get_remote() returns a connected RemoteHost (asking the user if needed) or None.
    """

    def __init__(self, parent, get_remote):
        wx.Panel.__init__(self, parent)
        self.get_remote = get_remote
        self.remote = None
        self.sftp = None
        self.path = "/"
        self.entries = []

        self.up_button = wx.BitmapButton(self, bitmap=art(wx.ART_GO_UP))
        self.up_button.SetToolTip("Parent folder")
        self.path_box = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.path_box.SetHint("Remote path, e.g. /var/log")
        self.refresh_button = wx.BitmapButton(self, bitmap=art(ART_REFRESH))
        self.refresh_button.SetToolTip("Refresh")
        self.upload_button = wx.Button(self, label="Upload...")
        self.download_button = wx.Button(self, label="Download...")
        self.connect_button = wx.Button(self, label="Connect")

        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(self.up_button, 0, wx.RIGHT, 4)
        row.Add(self.path_box, 1, wx.ALIGN_CENTER_VERTICAL)
        row.Add(self.refresh_button, 0, wx.LEFT, 4)
        row.Add(self.upload_button, 0, wx.LEFT, 8)
        row.Add(self.download_button, 0, wx.LEFT, 4)
        row.Add(self.connect_button, 0, wx.LEFT, 8)

        self.files = wx.ListCtrl(self, style=wx.LC_REPORT)
        for index, (heading, width) in enumerate((("Name", 320), ("Size", 90), ("Modified", 140), ("Permissions", 100))):
            self.files.InsertColumn(index, heading, width=self.FromDIP(width))
        images = wx.ImageList(16, 16)
        images.Add(wx.ArtProvider.GetBitmap(wx.ART_FOLDER, wx.ART_OTHER, wx.Size(16, 16)))
        images.Add(wx.ArtProvider.GetBitmap(wx.ART_NORMAL_FILE, wx.ART_OTHER, wx.Size(16, 16)))
        self.files.AssignImageList(images, wx.IMAGE_LIST_SMALL)

        self.status = wx.StaticText(self, label="Not connected.")

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 6)
        sizer.Add(self.files, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        sizer.Add(self.status, 0, wx.EXPAND | wx.ALL, 6)
        self.SetSizer(sizer)

        self.up_button.Bind(wx.EVT_BUTTON, lambda evt: self.Navigate(posixpath.dirname(self.path) or "/"))
        self.path_box.Bind(wx.EVT_TEXT_ENTER, lambda evt: self.Navigate(self.path_box.GetValue().strip() or "/"))
        self.refresh_button.Bind(wx.EVT_BUTTON, lambda evt: self.Navigate(self.path))
        self.upload_button.Bind(wx.EVT_BUTTON, self.OnUpload)
        self.download_button.Bind(wx.EVT_BUTTON, self.OnDownload)
        self.connect_button.Bind(wx.EVT_BUTTON, lambda evt: self.Connect())
        self.files.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.OnActivate)
        self.EnableControls()

    def IsConnected(self):
        return self.sftp is not None

    def EnableControls(self):
        connected = self.IsConnected()
        for ctrl in (self.up_button, self.path_box, self.refresh_button, self.upload_button,
                     self.download_button, self.files):
            ctrl.Enable(connected)
        self.connect_button.Show(not connected)
        self.Layout()

    def Activate(self):
        """Called when the tab is shown: connect the first time."""
        if not self.IsConnected():
            self.Connect()

    def Connect(self):
        remote = self.get_remote()
        if remote is None:
            self.status.SetLabel("Not connected.")
            return
        try:
            self.sftp = remote.open_sftp()
        except (OSError, paramiko.SSHException) as exception:
            self.status.SetLabel("Cannot open SFTP: %s" % exception)
            return
        self.remote = remote
        self.EnableControls()
        self.Navigate(self.sftp.normalize("."))

    def Disconnect(self):
        if self.sftp:
            try:
                self.sftp.close()
            except (OSError, paramiko.SSHException):
                pass
        self.sftp = None
        self.remote = None
        self.entries = []
        self.files.DeleteAllItems()
        self.status.SetLabel("Not connected.")
        self.EnableControls()

    def Navigate(self, path):
        if not self.IsConnected():
            return
        try:
            path = self.sftp.normalize(path)
            entries = self.sftp.listdir_attr(path)
        except (OSError, paramiko.SSHException) as exception:
            if not self.remote.is_connected():
                self.Disconnect()
                self.status.SetLabel("Connection lost.")
                return
            wx.MessageBox("Cannot open %s:\n%s" % (path, exception), "Remote Files", wx.OK | wx.ICON_WARNING,
                          self.GetTopLevelParent())
            return
        for entry in entries:
            entry.is_dir = self._is_dir(path, entry)
        entries.sort(key=lambda e: (not e.is_dir, e.filename.lower()))

        self.path = path
        self.entries = entries
        self.path_box.SetValue(path)
        self.files.DeleteAllItems()
        for index, entry in enumerate(entries):
            self.files.InsertItem(index, entry.filename, 0 if entry.is_dir else 1)
            self.files.SetItem(index, 1, "" if entry.is_dir else _size(entry.st_size or 0))
            self.files.SetItem(index, 2, strftime("%Y-%m-%d %H:%M", localtime(entry.st_mtime or 0)))
            self.files.SetItem(index, 3, stat.filemode(entry.st_mode or 0))
        where = "%s@%s" % (self.remote.username, self.remote.hostname) if hasattr(self.remote, "client") else "this PC"
        self.status.SetLabel("%s: %s  -  %d items" % (where, path, len(entries)))

    def _is_dir(self, folder, entry):
        mode = entry.st_mode or 0
        if stat.S_ISLNK(mode):
            try:
                mode = self.sftp.stat(posixpath.join(folder, entry.filename)).st_mode or 0
            except (OSError, paramiko.SSHException):
                return False
        return stat.S_ISDIR(mode)

    def OnActivate(self, event):
        entry = self.entries[event.GetIndex()]
        if entry.is_dir:
            self.Navigate(posixpath.join(self.path, entry.filename))
        else:
            self.DownloadEntries([entry])

    def SelectedEntries(self):
        selected = []
        index = self.files.GetFirstSelected()
        while index != -1:
            selected.append(self.entries[index])
            index = self.files.GetNextSelected(index)
        return selected

    def OnDownload(self, event):
        files = [e for e in self.SelectedEntries() if not e.is_dir]
        if not files:
            wx.MessageBox("Select one or more files to download.", "Download", wx.OK | wx.ICON_INFORMATION,
                          self.GetTopLevelParent())
            return
        self.DownloadEntries(files)

    def DownloadEntries(self, entries):
        dialog = wx.DirDialog(self, "Download to folder:")
        folder = dialog.GetPath() if dialog.ShowModal() == wx.ID_OK else None
        dialog.Destroy()
        if not folder:
            return
        jobs = [(posixpath.join(self.path, e.filename), os.path.join(folder, _local_name(e.filename))) for e in entries]
        existing = [local for _, local in jobs if os.path.exists(local)]
        if existing and not GUIManager.ConfirmGUI(self, "%d file(s) already exist in %s. Overwrite?" % (len(existing), folder), "Download"):
            return
        self.Transfer("Downloading", [("get", remote, local) for remote, local in jobs])

    def OnUpload(self, event):
        dialog = wx.FileDialog(self, "Upload files to " + self.path, style=wx.FD_OPEN | wx.FD_MULTIPLE | wx.FD_FILE_MUST_EXIST)
        paths = dialog.GetPaths() if dialog.ShowModal() == wx.ID_OK else []
        dialog.Destroy()
        if not paths:
            return
        names = {e.filename for e in self.entries}
        jobs = [(local, posixpath.join(self.path, os.path.basename(local))) for local in paths]
        existing = [remote for local, remote in jobs if os.path.basename(local) in names]
        if existing and not GUIManager.ConfirmGUI(self, "%d file(s) already exist in %s. Overwrite?" % (len(existing), self.path), "Upload"):
            return
        self.Transfer("Uploading", [("put", local, remote) for local, remote in jobs])

    def Transfer(self, verb, jobs):
        """Run SFTP transfers on a worker thread, on a separate SFTP channel."""
        remote = self.remote

        def status(text):
            wx.CallAfter(lambda: self and self.status.SetLabel(text))

        def worker():
            errors = []
            try:
                sftp = remote.open_sftp()
            except (OSError, paramiko.SSHException) as exception:
                errors.append(str(exception))
                sftp = None
            if sftp:
                try:
                    for index, (kind, source, target) in enumerate(jobs, 1):
                        label = "%s %d/%d: %s" % (verb, index, len(jobs), posixpath.basename(source.replace("\\", "/")))
                        progress = lambda done, total, label=label: status("%s (%s of %s)" % (label, _size(done), _size(total)))
                        try:
                            if kind == "get":
                                sftp.get(source, target, callback=progress)
                            else:
                                sftp.put(source, target, callback=progress)
                        except (OSError, paramiko.SSHException) as exception:
                            errors.append("%s: %s" % (source, exception))
                finally:
                    sftp.close()
            wx.CallAfter(lambda: self and self.TransferDone(verb, len(jobs), errors))

        threading.Thread(target=worker, daemon=True).start()

    def TransferDone(self, verb, count, errors):
        if verb == "Uploading":
            self.Navigate(self.path)
        self.status.SetLabel("%s finished: %d ok, %d failed" % (verb, count - len(errors), len(errors)))
        if errors:
            wx.MessageBox("\n".join(errors), verb + " failed", wx.OK | wx.ICON_WARNING, self.GetTopLevelParent())


class RemoteFileDialog(wx.Dialog):
    """Pick a file on a host over SFTP (e.g. the script a task runs). GetPath() gives its full path."""

    FILTERS = [("All files", None), ("Shell scripts (*.sh)", (".sh",)),
               ("Scripts (*.sh, *.py, *.pl)", (".sh", ".bash", ".py", ".pl"))]

    def __init__(self, parent, remote, current="", title="Choose a script on the host"):
        wx.Dialog.__init__(self, parent, title=title, size=parent.FromDIP(wx.Size(720, 520)),
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.remote = remote
        self.sftp = remote.open_sftp()
        self.path = "/"
        self.entries = []

        up = wx.BitmapButton(self, bitmap=art(wx.ART_GO_UP))
        up.SetToolTip("Parent folder")
        self.path_box = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.filter = wx.Choice(self, choices=[label for label, _ in self.FILTERS])
        self.filter.SetSelection(0)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(up, 0, wx.RIGHT, 4)
        row.Add(self.path_box, 1, wx.ALIGN_CENTER_VERTICAL)
        row.Add(self.filter, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 8)

        self.files = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for index, (heading, width) in enumerate((("Name", 300), ("Size", 90), ("Modified", 140), ("Permissions", 100))):
            self.files.InsertColumn(index, heading, width=self.FromDIP(width))
        images = wx.ImageList(16, 16)
        images.Add(wx.ArtProvider.GetBitmap(wx.ART_FOLDER, wx.ART_OTHER, wx.Size(16, 16)))
        images.Add(wx.ArtProvider.GetBitmap(wx.ART_NORMAL_FILE, wx.ART_OTHER, wx.Size(16, 16)))
        self.files.AssignImageList(images, wx.IMAGE_LIST_SMALL)

        self.name_box = wx.TextCtrl(self)
        name_row = wx.BoxSizer(wx.HORIZONTAL)
        name_row.Add(wx.StaticText(self, label="File:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        name_row.Add(self.name_box, 1)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 8)
        sizer.Add(self.files, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        sizer.Add(name_row, 0, wx.EXPAND | wx.ALL, 8)
        sizer.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(sizer)
        self.CentreOnParent()

        up.Bind(wx.EVT_BUTTON, lambda evt: self.Navigate(posixpath.dirname(self.path) or "/"))
        self.path_box.Bind(wx.EVT_TEXT_ENTER, lambda evt: self.Navigate(self.path_box.GetValue().strip() or "/"))
        self.filter.Bind(wx.EVT_CHOICE, lambda evt: self.Navigate(self.path))
        self.files.Bind(wx.EVT_LIST_ITEM_SELECTED, self.OnSelect)
        self.files.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.OnActivate)
        self.Bind(wx.EVT_BUTTON, self.OnOK, id=wx.ID_OK)
        self.Bind(wx.EVT_WINDOW_DESTROY, self.OnDestroy)

        # Start where the current script is, else in the user's home folder
        start, name = None, ""
        if current:
            try:
                folder = posixpath.dirname(self.remote.resolve_path(self.sftp, current))
                self.sftp.stat(folder)
                start, name = folder, posixpath.basename(current)
            except (OSError, paramiko.SSHException):
                pass
        self.Navigate(start or self.sftp.normalize("."))
        self.name_box.SetValue(name)

    def OnDestroy(self, event):
        if event.GetEventObject() is self and self.sftp:
            try:
                self.sftp.close()
            except (OSError, paramiko.SSHException):
                pass
            self.sftp = None
        event.Skip()

    def Navigate(self, path):
        try:
            path = self.sftp.normalize(path)
            entries = self.sftp.listdir_attr(path)
        except (OSError, paramiko.SSHException) as exception:
            wx.MessageBox("Cannot open %s:\n%s" % (path, exception), self.GetTitle(), wx.OK | wx.ICON_WARNING, self)
            return
        extensions = self.FILTERS[self.filter.GetSelection()][1]
        shown = []
        for entry in entries:
            mode = entry.st_mode or 0
            if stat.S_ISLNK(mode):
                try:
                    mode = self.sftp.stat(posixpath.join(path, entry.filename)).st_mode or 0
                except (OSError, paramiko.SSHException):
                    continue
            entry.is_dir = stat.S_ISDIR(mode)
            if entry.is_dir or not extensions or entry.filename.lower().endswith(extensions):
                shown.append(entry)
        shown.sort(key=lambda e: (not e.is_dir, e.filename.lower()))
        self.path, self.entries = path, shown
        self.path_box.SetValue(path)
        self.files.DeleteAllItems()
        for index, entry in enumerate(shown):
            self.files.InsertItem(index, entry.filename, 0 if entry.is_dir else 1)
            self.files.SetItem(index, 1, "" if entry.is_dir else _size(entry.st_size or 0))
            self.files.SetItem(index, 2, strftime("%Y-%m-%d %H:%M", localtime(entry.st_mtime or 0)))
            self.files.SetItem(index, 3, stat.filemode(entry.st_mode or 0))

    def OnSelect(self, event):
        entry = self.entries[event.GetIndex()]
        if not entry.is_dir:
            self.name_box.SetValue(entry.filename)

    def OnActivate(self, event):
        entry = self.entries[event.GetIndex()]
        if entry.is_dir:
            self.Navigate(posixpath.join(self.path, entry.filename))
        else:
            self.name_box.SetValue(entry.filename)
            self.EndModal(wx.ID_OK) if self.IsModal() else None

    def GetPath(self):
        name = self.name_box.GetValue().strip()
        return posixpath.join(self.path, name) if name else ""

    def OnOK(self, event):
        name = self.name_box.GetValue().strip()
        if not name:
            wx.MessageBox("Select a file.", self.GetTitle(), wx.OK | wx.ICON_INFORMATION, self)
            return
        try:
            is_file = stat.S_ISREG(self.sftp.stat(self.GetPath()).st_mode or 0)
        except (OSError, paramiko.SSHException):
            is_file = False
        if not is_file:
            wx.MessageBox("%s is not a file on the host." % self.GetPath(), self.GetTitle(), wx.OK | wx.ICON_WARNING, self)
            return
        event.Skip()
