import wx


class ConsolePanel(wx.Panel):
    """Command output plus a one-line command entry for ad-hoc remote commands."""

    def __init__(self, parent, on_run, on_stop, on_open_logs):
        wx.Panel.__init__(self, parent)
        self.on_run = on_run

        self.output = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.HSCROLL | wx.NO_BORDER)
        self.output.SetFont(wx.Font(10, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))

        self.command = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.command.SetHint("Command to run on the host, e.g. df -h  (Enter to run)")
        self.run_button = wx.Button(self, label="Run")
        self.stop_button = wx.Button(self, label="Stop")
        clear_button = wx.Button(self, label="Clear")
        logs_button = wx.Button(self, label="Open Logs")
        logs_button.SetToolTip("Open the folder with the saved output of every run")
        self.stop_button.Disable()

        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(self.command, 1, wx.ALIGN_CENTER_VERTICAL)
        row.Add(self.run_button, 0, wx.LEFT, 4)
        row.Add(self.stop_button, 0, wx.LEFT, 4)
        row.AddSpacer(12)
        row.Add(clear_button, 0)
        row.Add(logs_button, 0, wx.LEFT, 4)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.output, 1, wx.EXPAND)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 6)
        self.SetSizer(sizer)

        self.command.Bind(wx.EVT_TEXT_ENTER, self.OnRun)
        self.run_button.Bind(wx.EVT_BUTTON, self.OnRun)
        self.stop_button.Bind(wx.EVT_BUTTON, lambda evt: on_stop())
        clear_button.Bind(wx.EVT_BUTTON, lambda evt: self.output.Clear())
        logs_button.Bind(wx.EVT_BUTTON, lambda evt: on_open_logs())

    def OnRun(self, event):
        command = self.command.GetValue().strip()
        if command and self.run_button.IsEnabled():
            self.on_run(command)

    def Append(self, text):
        self.output.AppendText(text)

    def SetRunning(self, running):
        self.run_button.Enable(not running)
        self.stop_button.Enable(running)
