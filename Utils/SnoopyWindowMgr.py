import configparser
import os
import re
import threading
import urllib.parse
from time import localtime, strftime

import paramiko
import wx
import wx.html
import wx.adv
import wx.lib.agw.advancedsplash as AS

from .ConsolePanel import ConsolePanel
from .Credentials import Credentials
from .GUIManager import GUIManager, art, ART_STOP
from .HelpText import welcome, help_text
from .HostStore import HostContext, HostStore, HOST_SECTION, RESERVED_SECTIONS, NAME_PATTERN
from .LocalHost import LocalHost, is_local, is_windows_script
from .Paths import CONF_FILE, HOSTS_DIR, HISTORY_FILE, KNOWN_HOSTS_FILE, LOG_DIR, image
from .RemoteFilesPanel import RemoteFilesPanel, RemoteFileDialog
from .RemoteHost import RemoteHost, HostKeyRejected, build_grep, build_zgrep, build_du, split_args

TOOLS_HISTORY = "~Tools"      # host history section for grep/zgrep/du inputs
LAST_RUN_HISTORY = "~LastRun"  # host history section: "category/task" = "time|exit code|log file"
CHOICE_SEPARATOR = " / "       # "host / category" in the task dialog
LOCAL_SCRIPT_WILDCARD = ("Scripts (*.sh;*.cmd;*.bat;*.ps1)|*.sh;*.cmd;*.bat;*.ps1|Shell scripts (*.sh)|*.sh|"
                         "Windows scripts (*.cmd;*.bat;*.ps1)|*.cmd;*.bat;*.ps1|All files (*.*)|*.*")

# Tree item data: (HOST, host), (CATEGORY, host, category), (TASK, host, category, task, script)
HOST = "host"
CATEGORY = "category"
TASK = "task"
IMAGE_HOST, IMAGE_CATEGORY, IMAGE_TASK = range(3)

# Notebook pages
PAGE_TASK, PAGE_CONSOLE, PAGE_FILES = range(3)
# Task tab pages
DETAILS_WELCOME, DETAILS_HOST, DETAILS_CATEGORY, DETAILS_TASK = range(4)


class SnoopyWindowMgr(wx.Frame):

    def __init__(self, parent, id=-1, title="", pos=wx.DefaultPosition,
                 size=wx.DefaultSize, style=wx.DEFAULT_FRAME_STYLE):

        # Create the MainFrame
        wx.Frame.__init__(self, parent, id, title, pos, size, style)

        self.store = HostStore(CONF_FILE, HOSTS_DIR)
        self.hosts = {}  # name -> HostContext, in config order
        self.feedback_email = ""
        self.help_dialog = None
        self._populating = False

        # Display the SplashScreen
        bitmap = wx.Bitmap(image("Snoopy.png"), wx.BITMAP_TYPE_PNG)
        AS.AdvancedSplash(self, bitmap=bitmap, timeout=1500)

        self.SetIcon(wx.Icon(image("SSIUtils.ico")))
        self.SetMinSize(self.FromDIP(wx.Size(700, 450)))

        self.CreateActions()
        self.CreateMenus()
        self.CreateToolbar()

        self.statusbar = self.CreateStatusBar(3, wx.STB_SIZEGRIP)
        self.statusbar.SetStatusWidths([-3, -3, -3])
        self.SetStatus("Ready")

        splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE | wx.SP_3DSASH)
        left = self.CreateActivitiesPanel(splitter)

        self.notebook = wx.Notebook(splitter)
        self.details = self.CreateDetailsBook(self.notebook)
        self.console_book = self.CreateHostBook(self.notebook, "Select a host on the left to see its console.")
        self.files_book = self.CreateHostBook(self.notebook, "Select a host on the left to browse its files.")
        self.notebook.AddPage(self.details, "Details")
        self.notebook.AddPage(self.console_book, "Console")
        self.notebook.AddPage(self.files_book, "Remote Files")
        self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.OnPageChanged)

        splitter.SetMinimumPaneSize(self.FromDIP(180))
        splitter.SplitVertically(left, self.notebook, self.FromDIP(280))

        self.Bind(wx.EVT_CLOSE, self.OnClose)

        self.ReloadConfig(startup=True)

    # ------------------------------------------------------------------
    # Actions, menus and toolbar

    def CreateActions(self):
        """Every command once: menus, toolbar and buttons share these ids, and
        EVT_UPDATE_UI enables them only when they make sense."""
        self.ids = {}
        self.action_info = {}
        host = self.CurrentHost
        actions = [
            # key, label, shortcut, help, icon, handler, enabled
            ("new_host", "New &Host...", "Ctrl+Shift+H", "Add a remote host", wx.ART_HARDDISK, self.OnNewHost, None),
            ("new_category", "New &Category...", "Ctrl+Shift+N", "Add a category to the selected host",
             image("Category.png"), self.OnCreateCategory, lambda: bool(host() and host().config is not None)),
            ("new_task", "&New Task...", "Ctrl+N", "Add a task to a category", image("Task.png"), self.OnNewTask,
             lambda: bool(self.TaskTargets())),
            ("settings", "&Settings...", "", "Feedback e-mail address", image("Config.png"), self.OnSettings, None),
            ("exit", "E&xit", "", "Close Snoopy", wx.ART_QUIT, lambda evt: self.Close(), None),
            ("run", "&Run Task", "F5", "Run the selected task on its host", wx.ART_GO_FORWARD, self.OnRunSelected,
             lambda: self.SelectedKind() == TASK and not host().job),
            ("stop", "&Stop", "Shift+F5", "Stop the command running on the selected host", ART_STOP,
             self.OnStopJob, lambda: bool(host() and host().job)),
            ("edit", "&Edit...", "F2", "Edit the selected host, category or task", wx.ART_EDIT, self.OnEditSelected,
             lambda: self.SelectedKind() is not None),
            ("delete", "&Delete...", "", "Delete the selected host, category or task", wx.ART_DELETE,
             self.OnDeleteSelected, lambda: self.SelectedKind() is not None),
            ("search", "Searc&h", "Ctrl+F", "Filter the tree", wx.ART_FIND, lambda evt: self.search.SetFocus(), None),
            ("grep", "&Grep...", "Ctrl+G", "Search files on the selected host", wx.ART_FIND_AND_REPLACE, self.onGrep,
             lambda: bool(host() and not host().job)),
            ("zgrep", "&Zgrep...", "Ctrl+Shift+G", "Search compressed files on the selected host", wx.ART_FIND_AND_REPLACE, self.onZGrep,
             lambda: bool(host() and not host().job)),
            ("du", "&Disk Usage...", "Ctrl+D", "Show folder sizes on the selected host", wx.ART_LIST_VIEW, self.onDU,
             lambda: bool(host() and not host().job)),
            ("connect", "&Connect", "", "Connect to the selected host", wx.ART_TICK_MARK, self.OnConnect,
             lambda: bool(host() and not host().IsConnected())),
            ("disconnect", "D&isconnect", "", "Close the connection to the selected host", wx.ART_CROSS_MARK, self.OnDisconnect,
             lambda: bool(host() and host().IsConnected() and not host().job)),
            ("disconnect_all", "Disconnect &All", "", "Close every connection", wx.ART_CLOSE, self.OnDisconnectAll,
             lambda: any(c.IsConnected() and not c.job for c in self.hosts.values())),
            ("view_details", "&Details", "Ctrl+1", "Show the Details tab", wx.ART_REPORT_VIEW,
             lambda evt: self.notebook.SetSelection(PAGE_TASK), None),
            ("view_console", "&Console", "Ctrl+2", "Show the selected host's console", image("Command.png"),
             lambda evt: self.ShowConsole(focus=True), None),
            ("view_files", "&Remote Files", "Ctrl+3", "Browse the selected host's files", wx.ART_FOLDER_OPEN,
             lambda evt: self.notebook.SetSelection(PAGE_FILES), None),
            ("help", "&Help", "F1", "Show help", wx.ART_HELP, self.OnHelp, None),
            ("feedback", "Send &Feedback...", "", "E-mail feedback about Snoopy", wx.ART_TIP, self.OnSendFeedback, None),
            ("about", "&About Snoopy", "", "Version information", image("About.png"), self.OnAbout, None),
        ]
        for key, label, shortcut, help, icon, handler, enabled in actions:
            action_id = {"exit": wx.ID_EXIT, "about": wx.ID_ABOUT, "help": wx.ID_HELP}.get(key) or wx.NewIdRef()
            self.ids[key] = action_id
            self.action_info[key] = (label, shortcut, help, icon)
            self.Bind(wx.EVT_MENU, handler, id=action_id)
            self.Bind(wx.EVT_BUTTON, handler, id=action_id)
            if enabled:
                self.Bind(wx.EVT_UPDATE_UI, lambda evt, enabled=enabled: evt.Enable(bool(enabled())), id=action_id)

    def Icon(self, icon):
        """A 16px (and 32px for high-DPI) bitmap bundle from a PNG file path or a wx.ART_* id."""
        if icon is None:
            return None
        if isinstance(icon, str) and os.path.isfile(icon):
            picture = wx.Image(icon, wx.BITMAP_TYPE_PNG)
            return wx.BitmapBundle.FromBitmaps([
                wx.Bitmap(picture.Scale(size, size, wx.IMAGE_QUALITY_HIGH)) for size in (16, 24, 32)])
        return art(icon)

    def MenuItem(self, menu, key):
        label, shortcut, help, icon = self.action_info[key]
        item = wx.MenuItem(menu, self.ids[key], label + ("\t" + shortcut if shortcut else ""), help)
        if icon is not None:
            item.SetBitmap(self.Icon(icon))
        menu.Append(item)

    def CreateMenus(self):
        layout = [
            ("&File", ["new_host", "new_category", "new_task", None, "settings", None, "exit"]),
            ("&Task", ["run", "stop", None, "edit", "delete", None, "search"]),
            ("&Host", ["connect", "disconnect", "disconnect_all", None, "grep", "zgrep", "du"]),
            ("&View", ["view_details", "view_console", "view_files"]),
            ("&Help", ["help", "feedback", None, "about"]),
        ]
        mb = wx.MenuBar()
        for title, keys in layout:
            menu = wx.Menu()
            for key in keys:
                if key is None:
                    menu.AppendSeparator()
                else:
                    self.MenuItem(menu, key)
            mb.Append(menu, title)
        self.SetMenuBar(mb)

    def CreateToolbar(self):
        tb = self.CreateToolBar(wx.TB_HORIZONTAL | wx.TB_FLAT | wx.TB_HORZ_TEXT | wx.TB_NODIVIDER)
        labels = {"run": "Run", "stop": "Stop", "new_host": "New Host", "new_category": "New Category",
                  "new_task": "New Task", "edit": "Edit", "delete": "Delete", "grep": "Grep"}
        for key in ["run", "stop", None, "new_host", "new_category", "new_task", "edit", "delete", None, "grep"]:
            if key is None:
                tb.AddSeparator()
                continue
            label, shortcut, help, icon = self.action_info[key]
            tooltip = help + (" (%s)" % shortcut if shortcut else "")
            tb.AddTool(self.ids[key], labels[key], self.Icon(icon), tooltip)
        tb.Realize()

    # ------------------------------------------------------------------
    # Layout

    def CreateActivitiesPanel(self, parent):
        panel = wx.Panel(parent)
        self.search = wx.SearchCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.search.SetDescriptiveText("Search hosts and tasks")
        self.search.ShowCancelButton(True)
        self.search.Bind(wx.EVT_TEXT, lambda evt: self.PopulateTree())
        self.search.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, lambda evt: self.search.SetValue(""))

        self.tree = wx.TreeCtrl(panel, style=wx.TR_DEFAULT_STYLE | wx.TR_HIDE_ROOT | wx.TR_LINES_AT_ROOT |
                                wx.TR_FULL_ROW_HIGHLIGHT | wx.BORDER_THEME)
        imglist = wx.ImageList(16, 16, True, 3)
        imglist.Add(wx.ArtProvider.GetBitmap(wx.ART_HARDDISK, wx.ART_OTHER, wx.Size(16, 16)))
        imglist.Add(wx.ArtProvider.GetBitmap(wx.ART_FOLDER, wx.ART_OTHER, wx.Size(16, 16)))
        imglist.Add(wx.ArtProvider.GetBitmap(wx.ART_EXECUTABLE_FILE, wx.ART_OTHER, wx.Size(16, 16)))
        self.tree.AssignImageList(imglist)
        self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.OnTreeSelChanged)
        self.tree.Bind(wx.EVT_TREE_ITEM_ACTIVATED, self.OnTreeActivated)
        self.tree.Bind(wx.EVT_TREE_KEY_DOWN, self.OnTreeKey)
        self.tree.Bind(wx.EVT_CONTEXT_MENU, self.OnTreeContextMenu)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.search, 0, wx.EXPAND | wx.ALL, 6)
        sizer.Add(self.tree, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        panel.SetSizer(sizer)
        return panel

    def CreateHostBook(self, parent, placeholder):
        """A book with one page per host, plus a placeholder page when no host is selected."""
        book = wx.Simplebook(parent)
        page = wx.Panel(book)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.AddStretchSpacer()
        sizer.Add(wx.StaticText(page, label=placeholder), 0, wx.ALIGN_CENTER)
        sizer.AddStretchSpacer()
        page.SetSizer(sizer)
        book.AddPage(page, "none")
        return book

    def DetailsPage(self, book, rows, buttons):
        """A details page: a title, label/value rows and a row of action buttons."""
        gap = self.FromDIP(8)
        page = wx.Panel(book)
        title = wx.StaticText(page)
        title.SetFont(self.GetFont().Bold().Scaled(1.4))
        grid = wx.FlexGridSizer(cols=2, vgap=gap, hgap=2 * gap)
        grid.AddGrowableCol(1)
        for label, item in rows(page):
            grid.Add(wx.StaticText(page, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(item, 1, wx.EXPAND)
        row = wx.BoxSizer(wx.HORIZONTAL)
        for key, label in buttons:
            button = wx.Button(page, self.ids[key], label)
            if key in ("run", "connect"):
                button.SetBitmap(art(wx.ART_GO_FORWARD))
            row.Add(button, 0, wx.RIGHT, gap)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(title, 0, wx.ALL, 2 * gap)
        sizer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 2 * gap)
        sizer.Add(row, 0, wx.ALL, 2 * gap)
        page.SetSizer(sizer)
        book.AddPage(page, "")
        return title

    def CreateDetailsBook(self, parent):
        book = wx.Simplebook(parent)

        # Welcome page: nothing selected
        html = wx.html.HtmlWindow(book, style=wx.html.HW_SCROLLBAR_AUTO)
        if "gtk2" in wx.PlatformInfo or "gtk3" in wx.PlatformInfo:
            html.SetStandardFonts()
        html.SetPage(welcome)
        book.AddPage(html, "welcome")

        # Host page
        def host_rows(page):
            self.host_address = wx.StaticText(page)
            self.host_state = wx.StaticText(page)
            self.host_contents = wx.StaticText(page)
            return [("Address:", self.host_address), ("Status:", self.host_state), ("Contents:", self.host_contents)]
        self.host_title = self.DetailsPage(book, host_rows, [
            ("connect", "Connect"), ("disconnect", "Disconnect"), ("edit", "Edit..."),
            ("new_category", "New Category..."), ("delete", "Remove...")])

        # Category page
        def category_rows(page):
            self.category_host = wx.StaticText(page)
            self.category_info = wx.StaticText(page)
            return [("Host:", self.category_host), ("Tasks:", self.category_info)]
        self.category_title = self.DetailsPage(book, category_rows, [
            ("new_task", "New Task..."), ("edit", "Rename..."), ("delete", "Delete...")])

        # Task page
        def task_rows(page):
            self.task_host = wx.StaticText(page)
            self.task_category = wx.StaticText(page)
            self.task_script = wx.StaticText(page, style=wx.ST_ELLIPSIZE_MIDDLE)
            self.task_missing = wx.StaticText(page, label="Not found on this PC - use Edit to fix the path.")
            self.task_missing.SetForegroundColour(wx.Colour(200, 0, 0))
            script_box = wx.BoxSizer(wx.VERTICAL)
            script_box.Add(self.task_script, 0, wx.EXPAND)
            script_box.Add(self.task_missing, 0, wx.TOP, 2)
            self.param_box = wx.TextCtrl(page, style=wx.TE_PROCESS_ENTER)
            self.param_box.SetHint("Arguments passed to the script, e.g. -v \"two words\"")
            self.param_box.Bind(wx.EVT_TEXT_ENTER, self.OnRunSelected)
            self.last_run = wx.StaticText(page)
            self.open_log_button = wx.Button(page, label="Open log")
            self.open_log_button.Bind(wx.EVT_BUTTON, self.OnOpenLastLog)
            last_run_box = wx.BoxSizer(wx.HORIZONTAL)
            last_run_box.Add(self.last_run, 0, wx.ALIGN_CENTER_VERTICAL)
            last_run_box.Add(self.open_log_button, 0, wx.LEFT, self.FromDIP(8))
            return [("Host:", self.task_host), ("Category:", self.task_category), ("Script on host:", script_box),
                    ("Parameters:", self.param_box), ("Last run:", last_run_box)]
        self.task_title = self.DetailsPage(book, task_rows, [
            ("run", "Run"), ("edit", "Edit..."), ("delete", "Delete...")])
        return book

    def SetStatus(self, text):
        self.statusbar.SetStatusText(text, 0)

    def UpdateHostStatus(self, ctx=None):
        """Refresh the tree item, status bar and details page for a host's connection state."""
        for each in ([ctx] if ctx else self.hosts.values()):
            item = self.FindTreeItem(each.name)
            if item:
                self.tree.SetItemBold(item, each.IsConnected())
        current = self.CurrentHost()
        if current is None:
            text = "%d host(s), %d connected" % (len(self.hosts), sum(c.IsConnected() for c in self.hosts.values()))
        elif current.IsConnected():
            text = "%s: %s (%s)" % (current.name, "ready" if is_local(current.hostname) else "connected", current.address)
        else:
            text = "%s: not connected" % current.name
        self.statusbar.SetStatusText(text, 1)
        if self.SelectedKind() == HOST:
            self.ShowDetails()

    def ShowConsole(self, focus=False):
        self.notebook.SetSelection(PAGE_CONSOLE)
        ctx = self.CurrentHost()
        if focus and ctx:
            ctx.console.command.SetFocus()

    def ShowHostPanels(self):
        ctx = self.CurrentHost()
        for book, panel in ((self.console_book, ctx and ctx.console), (self.files_book, ctx and ctx.files)):
            index = book.FindPage(panel) if panel else 0
            book.SetSelection(max(index, 0))
        if ctx and self.notebook.GetSelection() == PAGE_FILES:
            self.ActivateFilesLater(ctx)

    def OnPageChanged(self, event):
        ctx = self.CurrentHost()
        if event.GetSelection() == PAGE_FILES and ctx:
            self.ActivateFilesLater(ctx)
        event.Skip()

    def ActivateFilesLater(self, ctx):
        """Connect the host's Remote Files panel once the UI has settled (the host may be gone by then)."""
        def activate():
            if ctx.files and ctx.name in self.hosts and self.CurrentHost() is ctx:
                ctx.files.Activate()
        wx.CallAfter(activate)

    # ------------------------------------------------------------------
    # Hosts and config

    def ReloadConfig(self, startup=False):
        """Re-read the global config and every host file, keeping connections and panels."""
        try:
            migrated = self.store.MigrateLegacy(HISTORY_FILE)
        except (OSError, configparser.Error) as exception:
            migrated = None
            wx.MessageBox("Could not convert Config.ini to the multi-host layout:\n" + str(exception),
                          "Config File", wx.OK | wx.ICON_ERROR)
        if migrated:
            wx.MessageBox('Snoopy now supports several hosts. Your existing host and tasks were moved to host '
                          '"%s" (Conf/Hosts). A copy of the old Config.ini was kept as Config.ini.bak.' % migrated,
                          "Config Updated", wx.OK | wx.ICON_INFORMATION)

        try:
            self.feedback_email = self.store.FeedbackEmail()
            entries = self.store.HostEntries()
        except configparser.Error as exception:
            wx.MessageBox('Config file could not be read:\n' + str(exception), 'Config File', wx.OK | wx.ICON_ERROR)
            entries = []

        by_path = {os.path.normcase(ctx.path): ctx for ctx in self.hosts.values()}
        hosts = {}
        for name, path in entries:
            ctx = by_path.pop(os.path.normcase(path), None) or HostContext(name, path)
            ctx.name = name
            ctx.Load()
            if ctx.remote and ctx.remote.key != ctx.key and not ctx.job:
                self.CloseConnection(ctx)
            self.EnsurePanels(ctx)
            hosts[name] = ctx
        for ctx in by_path.values():  # hosts that were removed
            self.CloseConnection(ctx)
            self.RemovePanels(ctx)
        self.hosts = hosts

        errors = [ctx.name + ": " + ctx.error for ctx in hosts.values() if ctx.error]
        if startup and errors:
            wx.MessageBox("\n".join(errors), "Host Files", wx.OK | wx.ICON_WARNING)
        if startup and not hosts:
            wx.MessageBox("No hosts are set up yet. Use File > New Host to add one.", "Welcome",
                          wx.OK | wx.ICON_INFORMATION)
        self.PopulateTree()

    def EnsurePanels(self, ctx):
        if ctx.console is None:
            ctx.console = ConsolePanel(self.console_book, lambda command, ctx=ctx: self.RunCommand(ctx, command),
                                       lambda ctx=ctx: self.StopJob(ctx), self.OnOpenLogs)
            self.console_book.AddPage(ctx.console, ctx.path)
            ctx.files = RemoteFilesPanel(self.files_book, lambda ctx=ctx: self.GetConnection(ctx))
            self.files_book.AddPage(ctx.files, ctx.path)

    def RemovePanels(self, ctx):
        for book, panel in ((self.console_book, ctx.console), (self.files_book, ctx.files)):
            index = book.FindPage(panel) if panel else wx.NOT_FOUND
            if index != wx.NOT_FOUND:
                book.DeletePage(index)
        ctx.console = ctx.files = None

    def TaskTargets(self):
        """All (host, category) pairs a task can belong to."""
        return [(ctx.name, category) for ctx in self.hosts.values() for category in ctx.Categories()]

    def SaveConfig(self, action):
        """Run a config change, reporting errors, then refresh the views."""
        try:
            action()
        except (OSError, configparser.Error) as exception:
            wx.MessageBox("Could not save the config file:\n" + str(exception), "Config File", wx.OK | wx.ICON_ERROR)
            return False
        self.ReloadConfig()
        return True

    def OnSettings(self, event):
        values = GUIManager.AskFields(self, "Settings", [
            {"name": "feedback", "label": "Feedback e-mail:", "value": self.feedback_email}],
            intro="Host connections are set per host: select a host and choose Edit.")
        if values is not None:
            self.SaveConfig(lambda: self.store.SetFeedbackEmail(values["feedback"]))

    def HostDialog(self, title, ctx=None):
        fields = [
            {"name": "name", "label": "Display name:", "value": ctx.name if ctx else ""},
            {"name": "hostname", "label": "Host name or IP:", "value": ctx.hostname if ctx else ""},
            {"name": "username", "label": "User name:", "value": ctx.username if ctx else ""},
            {"name": "port", "label": "SSH port:", "value": str(ctx.port if ctx else 22)},
            {"name": "password", "label": "Password:", "kind": "password"},
            {"name": "test", "label": "Test connection after saving", "kind": "check", "value": True},
        ]

        def validate(values):
            error = self.ValidateName("Host", values["name"], list(self.hosts), ctx.name if ctx else None)
            if error:
                return error
            if not values["hostname"] or re.search(r"\s", values["hostname"]):
                return "Enter a host name or IP address."
            if is_local(values["hostname"]):
                return None  # runs on this PC: no SSH user, port or password
            if not values["username"] or re.search(r"\s", values["username"]):
                return "Enter a user name."
            if not values["port"].isdigit() or not 0 < int(values["port"]) < 65536:
                return "The port must be a number between 1 and 65535."
            return None

        intro = ("Leave the password empty to keep the saved one." if ctx else
                 "The password is stored in Windows Credential Manager.") + \
            "\nUse localhost or 127.0.0.1 to run tasks on this PC (no SSH, user name or password needed)."
        return GUIManager.AskFields(self, title, fields, validate, intro)

    def AfterHostSaved(self, values):
        ctx = self.hosts.get(values["name"])
        if ctx is None:
            return
        if values["password"] and not is_local(ctx.hostname):
            Credentials.set_password(ctx.hostname, ctx.username, values["password"])
            if not ctx.job:
                self.CloseConnection(ctx)
        self.search.ChangeValue("")
        self.PopulateTree()
        self.SelectTreeItem(ctx.name)
        if values["test"] and self.GetConnection(ctx):
            message = ("%s runs on this PC (%s)." % (ctx.name, ctx.remote.description) if is_local(ctx.hostname)
                       else "Connected to %s (%s)." % (ctx.name, ctx.address))
            wx.MessageBox(message, "Connection", wx.OK | wx.ICON_INFORMATION)

    def OnNewHost(self, event):
        values = self.HostDialog("New Host")
        if values and self.SaveConfig(lambda: self.store.AddHost(values["name"], values["hostname"],
                                                                 values["username"], values["port"])):
            self.AfterHostSaved(values)

    def OnEditHost(self, event):
        ctx = self.CurrentHost()
        if ctx is None:
            return
        values = self.HostDialog("Edit Host", ctx)
        if not values:
            return

        def save():
            if values["name"] != ctx.name:
                self.store.RenameHost(ctx.name, values["name"])
            ctx.manager.WriteToConfig(HOST_SECTION, "HostName", values["hostname"])
            ctx.manager.WriteToConfig(HOST_SECTION, "UserName", values["username"])
            ctx.manager.WriteToConfig(HOST_SECTION, "Port", values["port"])

        if self.SaveConfig(save):
            self.AfterHostSaved(values)

    def OnDeleteHost(self, event):
        ctx = self.CurrentHost()
        if ctx is None:
            return
        if ctx.job:
            wx.MessageBox("'%s' is running on %s. Stop it first." % (ctx.job, ctx.name), "Remove Host",
                          wx.OK | wx.ICON_INFORMATION)
            return
        tasks = sum(len(ctx.Tasks(c)) for c in ctx.Categories())
        message = ('Remove host "%s" and its %d categories (%d tasks) from Snoopy?\n\n'
                   'Its host file is kept in Conf/Hosts, renamed to *.deleted-<date>, '
                   'and the script files are not touched.' % (ctx.name, len(ctx.Categories()), tasks))
        if GUIManager.ConfirmGUI(self, message, "Remove Host"):
            self.SaveConfig(lambda: self.store.RemoveHost(ctx.name))

    # ------------------------------------------------------------------
    # Connection

    def GetHostPassword(self, ctx):
        """Return the host password from the OS credential store, asking once if missing."""
        password = Credentials.get_password(ctx.hostname, ctx.username)
        if password is None:
            password = GUIManager.PasswordGUI("Password for %s (%s):" % (ctx.name, ctx.address), "Host Password", self)
            if password:
                Credentials.set_password(ctx.hostname, ctx.username, password)
                if not Credentials.persistent:
                    self.SetStatus("No system keyring found: the password is remembered until Snoopy closes")
        return password

    def AskTrustHostKey(self, hostname, key_type, key_fingerprint):
        message = ("Snoopy has not connected to %s before.\n\n"
                   "Key type: %s\nFingerprint: %s\n\n"
                   "Only trust this key if it matches the server's fingerprint. Trust it?"
                   % (hostname, key_type, key_fingerprint))
        return GUIManager.ConfirmGUI(self, message, "Unknown Host Key")

    def GetConnection(self, ctx):
        """Return a connected RemoteHost for a host, connecting (and asking for a password) if needed."""
        if ctx is None:
            return None
        if ctx.config is None:
            wx.MessageBox(ctx.error or "The host file could not be read.", ctx.name, wx.OK | wx.ICON_WARNING)
            return None
        if is_local(ctx.hostname):
            return self.GetLocalConnection(ctx)
        if not ctx.hostname or not ctx.username:
            wx.MessageBox("Set the host name and user name for %s first (select it and choose Edit)." % ctx.name,
                          "No Host", wx.OK | wx.ICON_INFORMATION)
            return None
        if ctx.remote and ctx.remote.key == ctx.key and ctx.remote.is_connected():
            return ctx.remote
        self.CloseConnection(ctx)

        remote = RemoteHost(ctx.hostname, ctx.username, ctx.port, known_hosts_file=KNOWN_HOSTS_FILE)
        try:
            for attempt in range(3):
                password = self.GetHostPassword(ctx)
                if not password:
                    return None
                self.SetStatus("Connecting to %s..." % ctx.name)
                wx.BeginBusyCursor()
                try:
                    remote.connect(password, self.AskTrustHostKey)
                except paramiko.AuthenticationException:
                    Credentials.delete_password(ctx.hostname, ctx.username)
                    wx.EndBusyCursor()
                    wx.MessageBox("Login to %s (%s) failed. Please enter the password again." % (ctx.name, ctx.address),
                                  "Login Failed", wx.OK | wx.ICON_WARNING)
                    continue
                except HostKeyRejected:
                    wx.EndBusyCursor()
                    self.SetStatus("Host key for %s not trusted" % ctx.name)
                    return None
                except paramiko.BadHostKeyException:
                    wx.EndBusyCursor()
                    wx.MessageBox("The host key for %s has CHANGED since the last connection.\n\n"
                                  "This can mean someone is intercepting the connection. Snoopy will not connect.\n"
                                  "If the server was legitimately reinstalled, remove its entry from:\n%s"
                                  % (ctx.hostname, KNOWN_HOSTS_FILE), "Host Key Changed", wx.OK | wx.ICON_ERROR)
                    self.SetStatus("Host key mismatch for %s" % ctx.name)
                    return None
                except (OSError, paramiko.SSHException) as exception:
                    wx.EndBusyCursor()
                    wx.MessageBox("Could not connect to %s (%s:%d)\n\n%s" % (ctx.name, ctx.hostname, ctx.port, exception),
                                  "Connection Failed", wx.OK | wx.ICON_ERROR)
                    self.SetStatus("Connection to %s failed" % ctx.name)
                    return None
                wx.EndBusyCursor()
                ctx.remote = remote
                self.SetStatus("Connected to %s" % ctx.name)
                return remote
            self.SetStatus("Login to %s failed" % ctx.name)
            return None
        finally:
            self.UpdateHostStatus(ctx)

    def GetLocalConnection(self, ctx):
        """localhost / 127.0.0.1: no SSH, password or host key - commands run on this PC through bash."""
        if isinstance(ctx.remote, LocalHost) and ctx.remote.key == ctx.key and ctx.remote.is_connected():
            return ctx.remote
        self.CloseConnection(ctx)
        local = LocalHost(ctx.hostname, ctx.username, ctx.port)
        try:
            local.connect()
        except OSError as exception:
            wx.MessageBox(str(exception), "Run on This PC", wx.OK | wx.ICON_WARNING)
            self.SetStatus("bash not found")
            self.UpdateHostStatus(ctx)
            return None
        ctx.remote = local
        self.SetStatus("%s runs on this PC (%s)" % (ctx.name, local.description))
        self.UpdateHostStatus(ctx)
        return local

    def CloseConnection(self, ctx):
        if ctx.files:
            ctx.files.Disconnect()
        if ctx.remote:
            ctx.remote.close()
            ctx.remote = None
        if ctx.name in self.hosts:
            self.UpdateHostStatus(ctx)

    def OnConnect(self, event):
        self.GetConnection(self.CurrentHost())

    def OnDisconnect(self, event):
        ctx = self.CurrentHost()
        if ctx and not ctx.job:
            self.CloseConnection(ctx)

    def OnDisconnectAll(self, event):
        for ctx in self.hosts.values():
            if not ctx.job:
                self.CloseConnection(ctx)

    # ------------------------------------------------------------------
    # Running commands

    def CallAfterSafe(self, fn, *args):
        def call():
            if self:  # the window may have been closed meanwhile
                fn(*args)
        try:
            wx.CallAfter(call)
        except (RuntimeError, AssertionError):
            pass

    def StartRemoteJob(self, ctx, label, work, on_done=None):
        """Run work(remote, output) for a host on a worker thread, streaming output to that host's
        console and a log file. on_done(code, error, log_path) is called on the UI thread afterwards."""
        if ctx.job:
            wx.MessageBox("'%s' is still running on %s. Stop it first." % (ctx.job, ctx.name), "Busy",
                          wx.OK | wx.ICON_INFORMATION)
            return
        remote = self.GetConnection(ctx)
        if remote is None:
            return

        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            safe_label = re.sub(r"[^A-Za-z0-9._-]+", "_", ctx.name + " " + label)[:80]
            log_path = os.path.join(LOG_DIR, strftime("%Y-%m-%d_%H-%M-%S", localtime()) + "_" + safe_label + ".log")
            log = open(log_path, "w", encoding="utf-8")
        except OSError as exception:
            wx.MessageBox("Cannot create log file:\n" + str(exception), "Log", wx.OK | wx.ICON_ERROR)
            return

        ctx.job = label
        if self.CurrentHost() is ctx:
            self.ShowConsole()
        ctx.console.SetRunning(True)
        header = "=== %s  [%s: %s]  %s ===\n" % (label, ctx.name, ctx.address, strftime("%Y-%m-%d %H:%M:%S", localtime()))
        log.write(header)
        ctx.console.Append(header)
        self.SetStatus("Running on %s: %s" % (ctx.name, label))

        def output(text):
            log.write(text)
            self.CallAfterSafe(ctx.console.Append, text)

        def worker():
            code, error = None, None
            try:
                code = work(remote, output)
            except Exception as exception:  # report anything to the console rather than lose it
                error = str(exception) or exception.__class__.__name__
            self.CallAfterSafe(self.OnJobFinished, ctx, label, log, log_path, code, error, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def OnJobFinished(self, ctx, label, log, log_path, code, error, on_done):
        if error:
            footer = "\n[error: %s]\n" % error
        elif code == -1:
            footer = "\n[stopped]\n"
        else:
            footer = "\n[finished with exit code %s]\n" % code
        footer += "[log: %s]\n\n" % log_path
        log.write(footer)
        log.close()
        if ctx.console:
            ctx.console.Append(footer)
            ctx.console.SetRunning(False)
        ctx.job = None
        self.SetStatus("%s - %s: %s" % (ctx.name, label,
                                        "error" if error else "stopped" if code == -1 else "exit code %s" % code))
        self.UpdateHostStatus(ctx)
        if on_done:
            on_done(code, error, log_path)

    def StopJob(self, ctx):
        if ctx and ctx.remote:
            ctx.remote.cancel()

    def OnStopJob(self, event=None):
        self.StopJob(self.CurrentHost())

    def OnOpenLogs(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        wx.LaunchDefaultApplication(LOG_DIR)

    def RunCommand(self, ctx, command):
        self.StartRemoteJob(ctx, command, lambda remote, output: remote.run(command, output))

    def ExecuteTask(self, ctx, category, task, script, parameters):
        # Remote scripts are checked on the host when the run starts (see RemoteHost.run_script)
        if is_local(ctx.hostname) and not os.path.isfile(script):
            wx.MessageBox("Script not found on this PC:\n" + script + "\n\nUse Edit to fix the path.",
                          "Run Task", wx.OK | wx.ICON_WARNING)
            return
        if is_windows_script(script) and os.name != "nt":
            wx.MessageBox("%s is a Windows script (.cmd, .bat or .ps1); it can't run on this system.\n\n"
                          "Use a .sh script instead." % os.path.basename(script),
                          "Run Task", wx.OK | wx.ICON_WARNING)
            return
        if is_windows_script(script) and not is_local(ctx.hostname):
            wx.MessageBox("%s is a Windows script, so it can only run on this PC.\n\n"
                          "Move the task to a host whose address is localhost (Edit > Host / category), "
                          "or use a .sh script for %s." % (os.path.basename(script), ctx.name),
                          "Run Task", wx.OK | wx.ICON_WARNING)
            return
        try:
            args = split_args(parameters)
        except ValueError as exception:
            wx.MessageBox("The parameters could not be read: %s" % exception, "Run Task", wx.OK | wx.ICON_WARNING)
            return
        ctx.SetHistory(category, task, parameters)

        def done(code, error, log_path):
            result = "error" if error else "stopped" if code == -1 else str(code)
            ctx.SetHistory(LAST_RUN_HISTORY, category + "/" + task,
                           "%s|%s|%s" % (strftime("%Y-%m-%d %H:%M", localtime()), result, log_path))
            if self.SelectedTask() == (ctx.name, category, task):
                self.ShowDetails()

        self.StartRemoteJob(ctx, "%s - %s" % (category, task),
                            lambda remote, output: remote.run_script(script, args, output), done)

    def OnRunSelected(self, event):
        data = self.ItemData()
        ctx = self.CurrentHost()
        if data and data[0] == TASK and ctx and not ctx.job:
            self.ExecuteTask(ctx, data[2], data[3], data[4], self.param_box.GetValue().strip())

    def GetLastRun(self, ctx, category, task):
        """Return (time, exit code, log file) of the task's last run, or None."""
        value = ctx.GetHistory(LAST_RUN_HISTORY, category + "/" + task, None)
        if not value or value.count("|") < 2:
            return None
        return tuple(value.split("|", 2))

    def OnOpenLastLog(self, event):
        data = self.ItemData()
        last = data and data[0] == TASK and self.GetLastRun(self.hosts[data[1]], data[2], data[3])
        if last and os.path.isfile(last[2]):
            wx.LaunchDefaultApplication(last[2])

    def AskTool(self, ctx, title, fields):
        for field in fields:
            if field.get("kind") != "check":
                field["value"] = ctx.GetHistory(TOOLS_HISTORY, title + "." + field["name"], field.get("value", ""))
        values = GUIManager.AskFields(self, "%s on %s" % (title, ctx.name), fields,
                                      lambda v: None if all(v[f["name"]] for f in fields if f.get("required")) else
                                      "Please fill in the required fields.")
        if values:
            for field in fields:
                if field.get("kind") != "check":
                    ctx.SetHistory(TOOLS_HISTORY, title + "." + field["name"], values[field["name"]])
        return values

    def onGrep(self, event):
        ctx = self.CurrentHost()
        values = ctx and self.AskTool(ctx, "Grep", [
            {"name": "pattern", "label": "Search for:", "required": True},
            {"name": "path", "label": "In path:", "value": "/var/log", "required": True},
            {"name": "recursive", "label": "Search sub-folders", "kind": "check", "value": True},
            {"name": "ignore_case", "label": "Ignore case", "kind": "check"},
        ])
        if values:
            self.RunCommand(ctx, build_grep(values["pattern"], values["path"], values["ignore_case"], values["recursive"]))

    def onZGrep(self, event):
        ctx = self.CurrentHost()
        values = ctx and self.AskTool(ctx, "Zgrep", [
            {"name": "pattern", "label": "Search for:", "required": True},
            {"name": "path", "label": "In files:", "value": "/var/log/*.gz", "required": True},
            {"name": "ignore_case", "label": "Ignore case", "kind": "check"},
        ])
        if values:
            self.RunCommand(ctx, build_zgrep(values["pattern"], values["path"], values["ignore_case"]))

    def onDU(self, event):
        ctx = self.CurrentHost()
        values = ctx and self.AskTool(ctx, "Disk Usage", [
            {"name": "path", "label": "Path:", "value": "/", "required": True},
            {"name": "per_folder", "label": "Show each sub-folder", "kind": "check", "value": True},
        ])
        if values:
            self.RunCommand(ctx, build_du(values["path"], values["per_folder"]))

    # ------------------------------------------------------------------
    # Window

    def OnClose(self, event):
        running = ["%s (%s)" % (ctx.job, ctx.name) for ctx in self.hosts.values() if ctx.job]
        if running and event.CanVeto() and not GUIManager.ConfirmGUI(
                self, "Still running:\n%s\n\nThese will be stopped. Close Snoopy?" % "\n".join(running), "Close Snoopy?"):
            event.Veto()
            return
        for ctx in self.hosts.values():
            self.CloseConnection(ctx)
        self._populating = True  # ignore tree events fired while the window is destroyed
        self.Destroy()

    def OnAbout(self, event):
        info = wx.adv.AboutDialogInfo()
        info.SetIcon(wx.Icon(image("SnoopySmall.png"), wx.BITMAP_TYPE_PNG))
        info.SetName('Snoopy')
        info.SetVersion('1.0')
        info.SetDescription("An integrated deployment management tool")
        wx.adv.AboutBox(info, self)

    def OnHelp(self, event):
        if not self.help_dialog:
            dlg = wx.Dialog(self, title="Snoopy Help", size=self.FromDIP(wx.Size(640, 620)),
                            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
            html = wx.html.HtmlWindow(dlg)
            html.SetPage(help_text)
            sizer = wx.BoxSizer(wx.VERTICAL)
            sizer.Add(html, 1, wx.EXPAND)
            sizer.Add(dlg.CreateStdDialogButtonSizer(wx.CLOSE), 0, wx.EXPAND | wx.ALL, 8)
            dlg.SetSizer(sizer)
            dlg.SetEscapeId(wx.ID_CLOSE)
            dlg.Bind(wx.EVT_BUTTON, lambda evt: dlg.Hide(), id=wx.ID_CLOSE)
            dlg.Bind(wx.EVT_CLOSE, lambda evt: dlg.Hide())
            self.help_dialog = dlg
        self.help_dialog.Show()
        self.help_dialog.Raise()

    def OnSendFeedback(self, event):
        if not self.feedback_email:
            wx.MessageBox("Set a feedback e-mail address in File > Settings first.", "Send Feedback",
                          wx.OK | wx.ICON_INFORMATION)
            return
        values = GUIManager.AskFields(self, "Send Feedback", [
            {"name": "subject", "label": "Subject:", "value": "Snoopy feedback"},
            {"name": "message", "label": "Message:", "kind": "multiline"},
        ], lambda v: None if v["message"].strip() else "Please write a message.",
            "Your e-mail program will open with this message to " + self.feedback_email + ".")
        if values is None:
            return
        url = "mailto:%s?%s" % (urllib.parse.quote(self.feedback_email, safe="@"),
                                urllib.parse.urlencode({"subject": values["subject"], "body": values["message"]},
                                                       quote_via=urllib.parse.quote))
        if not wx.LaunchDefaultBrowser(url):
            wx.MessageBox("No e-mail program is set up on this PC.", "Send Feedback", wx.OK | wx.ICON_WARNING)

    # ------------------------------------------------------------------
    # Tree and details

    def PopulateTree(self):
        """Rebuild the tree (hosts > categories > tasks), applying the search filter and keeping the selection."""
        selected = self.ItemData()
        needle = self.search.GetValue().strip().lower()
        tree = self.tree
        self._populating = True
        tree.Freeze()
        try:
            tree.DeleteAllItems()
            root = tree.AddRoot("Hosts")
            for ctx in self.hosts.values():
                host_match = not needle or needle in ctx.name.lower() or needle in ctx.hostname.lower()
                categories = []
                for category in ctx.Categories():
                    tasks = ctx.Tasks(category)
                    if not host_match and needle not in category.lower():
                        tasks = [(t, s) for t, s in tasks if needle in t.lower() or needle in s.lower()]
                        if not tasks:
                            continue
                    categories.append((category, tasks))
                if not host_match and not categories:
                    continue
                label = ctx.name + ("  (error)" if ctx.error else "")
                host_item = tree.AppendItem(root, label, IMAGE_HOST, data=(HOST, ctx.name))
                tree.SetItemBold(host_item, ctx.IsConnected())
                for category, tasks in categories:
                    item = tree.AppendItem(host_item, category, IMAGE_CATEGORY, data=(CATEGORY, ctx.name, category))
                    for task, script in tasks:
                        tree.AppendItem(item, task, IMAGE_TASK, data=(TASK, ctx.name, category, task, script))
                    tree.Expand(item)
                tree.Expand(host_item)
            if selected:
                self.SelectTreeItem(*selected[1:4])
        finally:
            tree.Thaw()
            self._populating = False
        self.ShowDetails()
        self.ShowHostPanels()
        self.UpdateHostStatus()

    def FindTreeItem(self, host, category=None, task=None):
        """Find the tree item for a host, a host's category, or a task."""
        path = [p for p in (host, category, task) if p is not None]
        item = self.tree.GetRootItem()
        if not item.IsOk():
            return None
        for depth, name in enumerate(path):
            child, cookie = self.tree.GetFirstChild(item)
            while child.IsOk():
                data = self.tree.GetItemData(child)
                if data and data[depth + 1] == name:
                    break
                child, cookie = self.tree.GetNextChild(item, cookie)
            if not child.IsOk():
                return None
            item = child
        return item

    def SelectTreeItem(self, host, category=None, task=None):
        item = self.FindTreeItem(host, category, task)
        if item:
            self.tree.SelectItem(item)
            self.tree.EnsureVisible(item)

    def ItemData(self, item=None):
        if item is None:
            item = self.tree.GetSelection()
        if not item or not item.IsOk() or item == self.tree.GetRootItem():
            return None
        return self.tree.GetItemData(item)

    def SelectedKind(self):
        data = self.ItemData()
        return data[0] if data else None

    def SelectedTask(self):
        """(host, category, task) of the selected task, or None."""
        data = self.ItemData()
        return tuple(data[1:4]) if data and data[0] == TASK else None

    def CurrentHost(self):
        """The host of the selected tree item; with nothing selected, the only host if there is just one."""
        data = self.ItemData()
        if data:
            return self.hosts.get(data[1])
        if len(self.hosts) == 1:
            return next(iter(self.hosts.values()))
        return None

    def ShowDetails(self):
        data = self.ItemData()
        ctx = self.hosts.get(data[1]) if data else None
        if not data or ctx is None:
            self.details.SetSelection(DETAILS_WELCOME)
            self.statusbar.SetStatusText("", 2)
            return
        kind = data[0]
        if kind == HOST:
            self.host_title.SetLabel(ctx.name)
            self.host_address.SetLabel(ctx.address)
            if ctx.job:
                state = "Running: " + ctx.job
            elif is_local(ctx.hostname):
                state = "Runs on this PC" + (" - " + ctx.remote.description if ctx.IsConnected() else " (no SSH)")
            else:
                state = "Connected" if ctx.IsConnected() else "Not connected"
            self.host_state.SetLabel(ctx.error or state)
            tasks = sum(len(ctx.Tasks(c)) for c in ctx.Categories())
            self.host_contents.SetLabel("%d categories, %d tasks" % (len(ctx.Categories()), tasks))
            self.details.SetSelection(DETAILS_HOST)
            self.statusbar.SetStatusText(ctx.address, 2)
        elif kind == CATEGORY:
            count = len(ctx.Tasks(data[2]))
            self.category_title.SetLabel(data[2])
            self.category_host.SetLabel("%s (%s)" % (ctx.name, ctx.address))
            self.category_info.SetLabel("%d task%s. Select a task to run it." % (count, "" if count == 1 else "s"))
            self.details.SetSelection(DETAILS_CATEGORY)
            self.statusbar.SetStatusText(ctx.address, 2)
        else:
            _, host, category, task, script = data
            self.task_title.SetLabel(task)
            self.task_host.SetLabel("%s (%s)" % (ctx.name, ctx.address))
            self.task_category.SetLabel(category)
            self.task_script.SetLabel(script)
            self.task_script.SetToolTip(script)
            self.task_missing.Show(is_local(ctx.hostname) and not os.path.isfile(script))
            self.param_box.ChangeValue(ctx.GetHistory(category, task))
            last = self.GetLastRun(ctx, category, task)
            if last:
                when, result, log_path = last
                result = "exit code " + result if result.lstrip("-").isdigit() else result
                self.last_run.SetLabel("%s  -  %s" % (when, result))
            else:
                self.last_run.SetLabel("Never")
            self.open_log_button.Show(bool(last and os.path.isfile(last[2])))
            self.details.SetSelection(DETAILS_TASK)
            self.statusbar.SetStatusText(script, 2)
        self.details.GetCurrentPage().Layout()

    def OnTreeSelChanged(self, event):
        if not self._populating:
            self.ShowDetails()
            self.ShowHostPanels()
            self.UpdateHostStatus()
            if self.notebook.GetSelection() != PAGE_TASK and not (self.CurrentHost() and self.CurrentHost().job):
                self.notebook.SetSelection(PAGE_TASK)

    def OnTreeActivated(self, event):
        data = self.ItemData(event.GetItem())
        if data and data[0] == TASK:
            self.OnRunSelected(event)
        else:
            event.Skip()

    def OnTreeKey(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_DELETE and self.SelectedKind():
            self.OnDeleteSelected(event)
        elif key == wx.WXK_F2 and self.SelectedKind():
            self.OnEditSelected(event)
        else:
            event.Skip()

    def OnTreeContextMenu(self, event):
        pos = event.GetPosition()
        if pos != wx.DefaultPosition:
            item, flags = self.tree.HitTest(self.tree.ScreenToClient(pos))
            if item.IsOk():
                self.tree.SelectItem(item)
            else:
                self.tree.UnselectAll()
        kind = self.SelectedKind()
        if kind == TASK:
            keys = ["run", None, "edit", "delete"]
        elif kind == CATEGORY:
            keys = ["new_task", None, "edit", "delete"]
        elif kind == HOST:
            keys = ["connect", "disconnect", None, "new_category", None, "edit", "delete"]
        else:
            keys = ["new_host"]
        menu = wx.Menu()
        for key in keys:
            if key is None:
                menu.AppendSeparator()
            else:
                self.MenuItem(menu, key)
        self.tree.PopupMenu(menu)
        menu.Destroy()

    # ------------------------------------------------------------------
    # Categories and tasks

    def ValidateName(self, kind, name, existing, current=None):
        if not name:
            return "%s name is required." % kind
        if not NAME_PATTERN.match(name):
            return "%s names may only contain letters, digits, spaces, '.', '_' and '-'." % kind
        if kind == "Category" and name.lower() in RESERVED_SECTIONS:
            return '"%s" is a reserved name.' % name
        others = {e.lower() for e in existing if current is None or e.lower() != current.lower()}
        if name.lower() in others:
            return 'A %s named "%s" already exists.' % (kind.lower(), name)
        return None

    def OnEditSelected(self, event):
        handler = {HOST: self.OnEditHost, CATEGORY: self.OnEditCategory, TASK: self.OnEditTask}.get(self.SelectedKind())
        if handler:
            handler(event)

    def OnDeleteSelected(self, event):
        handler = {HOST: self.OnDeleteHost, CATEGORY: self.OnDeleteCategory, TASK: self.OnDeleteTask}.get(self.SelectedKind())
        if handler:
            handler(event)

    def MoveTaskHistory(self, src, category, task, dst=None, new_category=None, new_task=None):
        """Move (or, without a destination, delete) a task's remembered parameters and last run."""
        entries = [(category, task, new_category, new_task),
                   (LAST_RUN_HISTORY, category + "/" + task, LAST_RUN_HISTORY,
                    new_category and new_category + "/" + new_task)]
        for section, key, new_section, new_key in entries:
            value = src.GetHistory(section, key, None)
            if value is None:
                continue
            try:
                src.history.RemoveFromConfig(section, key)
                if dst and new_key:
                    dst.history.WriteToConfig(new_section, new_key, value)
            except (OSError, configparser.Error):
                pass

    def OnCreateCategory(self, event):
        ctx = self.CurrentHost()
        if ctx is None or ctx.config is None:
            return
        values = GUIManager.AskFields(self, "New Category on " + ctx.name, [{"name": "name", "label": "Category name:"}],
                                      lambda v: self.ValidateName("Category", v["name"], ctx.Categories()))
        if values and self.SaveConfig(lambda: ctx.manager.AddSection(values["name"])):
            self.search.ChangeValue("")
            self.PopulateTree()
            self.SelectTreeItem(ctx.name, values["name"])

    def OnEditCategory(self, event):
        data = self.ItemData()
        if not data or data[0] != CATEGORY:
            return
        ctx, old = self.hosts[data[1]], data[2]
        values = GUIManager.AskFields(self, "Rename Category", [{"name": "name", "label": "Category name:", "value": old}],
                                      lambda v: self.ValidateName("Category", v["name"], ctx.Categories(), old))
        if not values or values["name"] == old:
            return
        new = values["name"]
        tasks = [t for t, _ in ctx.Tasks(old)]
        if self.SaveConfig(lambda: ctx.manager.RenameSection(old, new)):
            for task in tasks:
                self.MoveTaskHistory(ctx, old, task, ctx, new, task)
            self.SelectTreeItem(ctx.name, new)

    def OnDeleteCategory(self, event):
        data = self.ItemData()
        if not data or data[0] != CATEGORY:
            return
        ctx, category = self.hosts[data[1]], data[2]
        tasks = [t for t, _ in ctx.Tasks(category)]
        message = 'Delete category "%s" from %s' % (category, ctx.name)
        message += " and its %d task(s)?" % len(tasks) if tasks else "?"
        message += "\n\nThe script files themselves are not deleted."
        if GUIManager.ConfirmGUI(self, message, "Delete Category") and \
                self.SaveConfig(lambda: ctx.manager.RemoveSection(category)):
            for task in tasks:
                self.MoveTaskHistory(ctx, category, task)
            self.SelectTreeItem(ctx.name)

    def TaskDialog(self, title, host="", category="", name="", script=""):
        targets = self.TaskTargets()
        if not targets:
            wx.MessageBox("Create a category first.", title, wx.OK | wx.ICON_INFORMATION)
            return None
        choices = [h + CHOICE_SEPARATOR + c for h, c in targets]
        lookup = dict(zip(choices, targets))

        def browse(dialog, current):
            """Pick the script on the selected host: over SFTP for a remote host, locally for this PC."""
            ctx = self.hosts[lookup[dialog.controls["target"].GetStringSelection()][0]]
            if is_local(ctx.hostname):
                return GUIManager.LocalFileGUI(dialog, current, LOCAL_SCRIPT_WILDCARD)
            remote = self.GetConnection(ctx)
            if remote is None:
                return None
            try:
                picker = RemoteFileDialog(dialog, remote, current, "Choose a script on %s (%s)" % (ctx.name, ctx.address))
            except (OSError, paramiko.SSHException) as exception:
                wx.MessageBox("Cannot browse %s:\n%s" % (ctx.name, exception), title, wx.OK | wx.ICON_ERROR, dialog)
                return None
            path = picker.GetPath() if picker.ShowModal() == wx.ID_OK else None
            picker.Destroy()
            return path

        fields = [
            {"name": "target", "label": "Host / category:", "kind": "choice", "choices": choices,
             "value": host + CHOICE_SEPARATOR + category},
            {"name": "name", "label": "Task name:", "value": name},
            {"name": "script", "label": "Script on host:", "kind": "file", "value": script, "browse": browse},
        ]

        def validate(values):
            target_host, target_category = lookup[values["target"]]
            current = name if (target_host, target_category) == (host, category) else None
            existing = [t for t, _ in self.hosts[target_host].Tasks(target_category)]
            error = self.ValidateName("Task", values["name"], existing, current)
            if error:
                return error
            if not values["script"]:
                return "Choose the script to run (Browse... lists the host's files)."
            if not is_local(self.hosts[target_host].hostname) and not values["script"].startswith(("/", "~/")):
                return "Enter the script's full path on the host, e.g. /opt/scripts/backup.sh or ~/bin/backup.sh."
            return None

        values = GUIManager.AskFields(self, title, fields, validate,
                                      "The script stays on the host and runs there. Browse... lists the host's files.")
        if not values:
            return None
        values["host"], values["category"] = lookup[values["target"]]
        ctx = self.hosts[values["host"]]
        if is_local(ctx.hostname):
            missing = not os.path.isfile(values["script"])
        else:  # only check if already connected; never prompt just for this
            missing = ctx.IsConnected() and not ctx.remote.script_exists(values["script"])
        if missing:
            wx.MessageBox("Note: the script was not found on %s:\n%s" % (ctx.name, values["script"]), title,
                          wx.OK | wx.ICON_WARNING)
        return values

    def OnNewTask(self, event):
        data = self.ItemData()
        host = data[1] if data else ""
        category = data[2] if data and data[0] != HOST else ""
        if data and data[0] == HOST:
            categories = self.hosts[host].Categories()
            category = categories[0] if categories else ""
        values = self.TaskDialog("New Task", host, category)
        if not values:
            return
        ctx = self.hosts[values["host"]]
        if self.SaveConfig(lambda: ctx.manager.WriteToConfig(values["category"], values["name"], values["script"])):
            self.search.ChangeValue("")
            self.PopulateTree()
            self.SelectTreeItem(ctx.name, values["category"], values["name"])

    def OnEditTask(self, event):
        data = self.ItemData()
        if not data or data[0] != TASK:
            return
        _, host, category, task, script = data
        values = self.TaskDialog("Edit Task", host, category, task, script)
        if not values:
            return
        src, dst = self.hosts[host], self.hosts[values["host"]]
        new_category, new_task, new_script = values["category"], values["name"], values["script"]

        def save():
            if (dst, new_category) != (src, category):
                src.manager.RemoveFromConfig(category, task)
                dst.manager.WriteToConfig(new_category, new_task, new_script)
            else:
                src.manager.RenameOption(category, task, new_task, new_script)

        if self.SaveConfig(save):
            if (dst, new_category, new_task) != (src, category, task):
                self.MoveTaskHistory(src, category, task, dst, new_category, new_task)
            self.SelectTreeItem(dst.name, new_category, new_task)
            self.ShowDetails()

    def OnDeleteTask(self, event):
        data = self.ItemData()
        if not data or data[0] != TASK:
            return
        _, host, category, task, script = data
        ctx = self.hosts[host]
        if GUIManager.ConfirmGUI(self, 'Delete task "%s" from %s / %s?\n\nThe script file itself is not deleted.'
                                 % (task, ctx.name, category), "Delete Task") and \
                self.SaveConfig(lambda: ctx.manager.RemoveFromConfig(category, task)):
            self.MoveTaskHistory(ctx, category, task)
            self.SelectTreeItem(ctx.name, category)
