import wx
from Utils.SnoopyWindowMgr import SnoopyWindowMgr


class SnoopyApp(wx.App):
    def OnInit(self):
        win = SnoopyWindowMgr(None, wx.ID_ANY, "Snoopy - Beta", size=(890, 705))
        win.Show()
        win.Center()
        win.Maximize(True)
        return True


if __name__ == '__main__':
    app = SnoopyApp(False)
    app.MainLoop()
