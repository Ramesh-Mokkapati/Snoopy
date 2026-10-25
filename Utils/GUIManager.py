import wx
import os


# Icons added in wxWidgets 3.3 (wxPython 4.3); older wxPython (e.g. Linux distro
# packages with 4.2) gets the closest older icon instead.
ART_STOP = getattr(wx, "ART_STOP", wx.ART_CROSS_MARK)
ART_REFRESH = getattr(wx, "ART_REFRESH", wx.ART_REDO)


def art(art_id, size=16, client=wx.ART_TOOLBAR):
    """A standard icon as a DPI-aware bitmap bundle."""
    return wx.ArtProvider.GetBitmapBundle(art_id, client, wx.Size(size, size))


class FieldsDialog(wx.Dialog):
    """A simple form. Each field is a dict with:
        name, label, kind ('text', 'password', 'multiline', 'check', 'choice', 'file'),
        value (default), choices (for 'choice'), wildcard (for 'file').
    validate(values) may return an error message to keep the dialog open.
    """

    def __init__(self, parent, title, fields, validate=None, intro=None):
        wx.Dialog.__init__(self, parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.fields = fields
        self.validate = validate
        self.controls = {}

        main = wx.BoxSizer(wx.VERTICAL)
        if intro:
            main.Add(wx.StaticText(self, label=intro), 0, wx.ALL, 10)

        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=8)
        grid.AddGrowableCol(1)
        for field in fields:
            kind = field.get("kind", "text")
            value = field.get("value", "")
            if kind == "check":
                grid.Add((0, 0))
                ctrl = wx.CheckBox(self, label=field["label"])
                ctrl.SetValue(bool(value))
                grid.Add(ctrl)
            else:
                grid.Add(wx.StaticText(self, label=field["label"]), 0, wx.ALIGN_CENTER_VERTICAL)
                item = None
                if kind == "choice":
                    ctrl = wx.Choice(self, choices=field["choices"])
                    if value in field["choices"]:
                        ctrl.SetStringSelection(value)
                    elif field["choices"]:
                        ctrl.SetSelection(0)
                elif kind == "password":
                    ctrl = wx.TextCtrl(self, value=value, style=wx.TE_PASSWORD, size=(360, -1))
                elif kind == "multiline":
                    ctrl = wx.TextCtrl(self, value=value, style=wx.TE_MULTILINE, size=(360, 140))
                elif kind == "file":
                    ctrl = wx.TextCtrl(self, value=value, size=(300, -1))
                    browse = wx.Button(self, label="Browse...")
                    browse.Bind(wx.EVT_BUTTON, lambda evt, c=ctrl, f=field: self.OnBrowse(c, f))
                    item = wx.BoxSizer(wx.HORIZONTAL)
                    item.Add(ctrl, 1, wx.EXPAND)
                    item.Add(browse, 0, wx.LEFT, 5)
                else:
                    ctrl = wx.TextCtrl(self, value=str(value), size=(360, -1))
                grid.Add(item or ctrl, 1, wx.EXPAND)
            self.controls[field["name"]] = ctrl

        main.Add(grid, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)
        main.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 10)
        self.SetSizerAndFit(main)
        self.CentreOnParent()
        self.Bind(wx.EVT_BUTTON, self.OnOK, id=wx.ID_OK)

    def OnBrowse(self, ctrl, field):
        """A file field's Browse button: field["browse"](dialog, current) may supply its own
        picker (e.g. for files on a remote host); otherwise a local file dialog is used."""
        current = ctrl.GetValue().strip()
        if field.get("browse"):
            path = field["browse"](self, current)
        else:
            path = GUIManager.LocalFileGUI(self, current, field.get("wildcard", "*.*"))
        if path:
            ctrl.SetValue(path)
            ctrl.SetInsertionPointEnd()

    def GetValues(self):
        values = {}
        for field in self.fields:
            ctrl = self.controls[field["name"]]
            kind = field.get("kind", "text")
            if kind == "check":
                values[field["name"]] = ctrl.GetValue()
            elif kind == "choice":
                values[field["name"]] = ctrl.GetStringSelection()
            elif kind in ("password", "multiline"):
                values[field["name"]] = ctrl.GetValue()
            else:
                values[field["name"]] = ctrl.GetValue().strip()
        return values

    def OnOK(self, event):
        if self.validate:
            error = self.validate(self.GetValues())
            if error:
                wx.MessageBox(error, self.GetTitle(), wx.OK | wx.ICON_WARNING, self)
                return
        event.Skip()


# Requires a running wx.App.
class GUIManager:

    @staticmethod
    def AskFields(parent, title, fields, validate=None, intro=None):
        """Show a FieldsDialog; return the values dict, or None if cancelled."""
        dlg = FieldsDialog(parent, title, fields, validate, intro)
        values = dlg.GetValues() if dlg.ShowModal() == wx.ID_OK else None
        dlg.Destroy()
        return values

    @staticmethod
    def LocalFileGUI(parent, current="", wildcard="*.*"):
        """Pick an existing file on this PC; returns its path or None."""
        folder = os.path.dirname(current) if current and os.path.isdir(os.path.dirname(current)) else os.getcwd()
        dialog = wx.FileDialog(parent, "Choose a file", folder, "", wildcard, wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        path = dialog.GetPath() if dialog.ShowModal() == wx.ID_OK else None
        dialog.Destroy()
        return path

    @staticmethod
    def PasswordGUI(message, caption, parent=None):
        sReturnValue = None
        dlg = wx.PasswordEntryDialog(parent, message, caption)
        dlg.CentreOnScreen()
        if dlg.ShowModal() == wx.ID_OK:
            sReturnValue = dlg.GetValue()
        dlg.Destroy()
        return sReturnValue

    @staticmethod
    def ConfirmGUI(parent, message, caption):
        dlg = wx.MessageDialog(parent, message, caption, wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)
        answer = dlg.ShowModal()
        dlg.Destroy()
        return answer == wx.ID_YES
