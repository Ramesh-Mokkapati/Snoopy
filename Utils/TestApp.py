# Manual tests. Run from the project folder with:  python -m Utils.TestApp [--gui]
import configparser
import os
import shutil
import sys
import tempfile

from .ConfigManager import ConfigManager
from .HostStore import HostContext, HostStore
from .Credentials import Credentials
from .ProcessManager import ProcessManager
from .TestRemote import TestCommandBuilders, TestRemoteHost, TestLocalHost, TestRemoteScriptsWithRealBash

# A single-host Config.ini as written by earlier versions of Snoopy, so the tests
# do not depend on (or touch) the real configuration.
SAMPLE_CONFIG = """[Host]
HostName = 10.0.0.1
UserName = admin

[Backup]
backup = E:/scripts/backup/backup.sh

[Cleanup]
pfiles = E:/scripts/cleanup/pfiles.sh

[Reports]
create_awr_report = E:/scripts/reports/create_awr_report.sh
"""


def write_sample_config(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(SAMPLE_CONFIG)


class TestApp:

    @staticmethod
    def TestPaths():
        """From source, data and resources are in the project folder; when packaged,
        data goes next to the executable and resources come from the bundle."""
        import importlib
        from . import Paths
        project = os.path.dirname(os.path.dirname(os.path.abspath(Paths.__file__)))
        assert Paths.APP_DIR == Paths.BUNDLE_DIR == project, (Paths.APP_DIR, Paths.BUNDLE_DIR)
        assert Paths.image("Snoopy.png") == os.path.join(project, "images", "Snoopy.png")
        assert os.path.isfile(Paths.image("Snoopy.png"))

        saved = (getattr(sys, "frozen", None), getattr(sys, "_MEIPASS", None), sys.executable)
        install = os.path.join(tempfile.gettempdir(), "Snoopy-install")
        bundle = os.path.join(install, "_internal")
        try:
            sys.frozen, sys._MEIPASS, sys.executable = True, bundle, os.path.join(install, "Snoopy.exe")
            importlib.reload(Paths)
            assert Paths.APP_DIR == install and Paths.BUNDLE_DIR == bundle, (Paths.APP_DIR, Paths.BUNDLE_DIR)
            assert Paths.CONF_FILE == os.path.join(install, "Conf", "Config.ini")
            assert Paths.HOSTS_DIR == os.path.join(install, "Conf", "Hosts")
            assert Paths.LOG_DIR == os.path.join(install, "logs")
            assert Paths.image("Snoopy.png") == os.path.join(bundle, "images", "Snoopy.png")
        finally:
            for name, value in (("frozen", saved[0]), ("_MEIPASS", saved[1])):
                if value is None:
                    if hasattr(sys, name):
                        delattr(sys, name)
                else:
                    setattr(sys, name, value)
            sys.executable = saved[2]
            importlib.reload(Paths)
        assert Paths.APP_DIR == project
        print("Paths: OK")

    @staticmethod
    def TestCredentials():
        host, user = "snoopy-selftest.invalid", "selftest"
        Credentials.set_password(host, user, "dummy-value")
        assert Credentials.get_password(host, user) == "dummy-value"
        Credentials.delete_password(host, user)
        assert Credentials.get_password(host, user) is None
        print("Credentials: OK (%s)" % ("system credential store" if Credentials.persistent
                                        else "no credential store: kept in memory for the session"))

    @staticmethod
    def TestConfigManager():
        # Work on a copy so the real config is never modified.
        tmpdir = tempfile.mkdtemp()
        try:
            confFile = os.path.join(tmpdir, "Config.ini")
            write_sample_config(confFile)
            configManager = ConfigManager(confFile)
            configManager.WriteToConfig('Backup', 'backupMUSDWH', 'E:/Projects/MAS-Prod/ant-mas/scripts.1/backup/backupMUSDWH.sh')
            configManager.WriteToConfig('Test', 'newTask', 'C:/scripts/100%_done.sh')

            sections = configManager.GetAllSections()
            assert 'Backup' in sections and 'Test' in sections, sections
            keys = configManager.GetAllSectionKeys('Backup')
            assert 'backupMUSDWH' in keys, keys  # case preserved
            assert configManager.GetValue('Test', 'newTask') == 'C:/scripts/100%_done.sh'  # no interpolation

            configManager.WriteToConfig('Test', 'second', 'b.sh')
            configManager.RenameOption('Test', 'newTask', 'renamed', 'a.sh')
            assert list(configManager.GetAllSectionKeys('Test').items()) == [('renamed', 'a.sh'), ('second', 'b.sh')]
            configManager.RemoveFromConfig('Test', 'second')
            assert configManager.GetAllSectionKeys('Test') == {'renamed': 'a.sh'}

            before = configManager.GetAllSections()
            configManager.RenameSection('Test', 'Tested')
            after = configManager.GetAllSections()
            assert after == [s if s != 'Test' else 'Tested' for s in before], after
            assert configManager.GetAllSectionKeys('Tested') == {'renamed': 'a.sh'}

            configManager.AddSection('Empty')
            try:
                configManager.AddSection('Empty')
                raise AssertionError("expected DuplicateSectionError")
            except configparser.DuplicateSectionError:
                pass
            configManager.RemoveSection('Empty')
            assert 'Empty' not in configManager.GetAllSections()

            missing = ConfigManager(os.path.join(tmpdir, "sub", "New.ini"))
            missing.WriteToConfig('A', 'b', 'c')  # creates the folder and file
            assert missing.GetValue('A', 'b') == 'c'
            print("ConfigManager: OK")
        finally:
            shutil.rmtree(tmpdir)

    @staticmethod
    def TestHostStore():
        tmpdir = tempfile.mkdtemp()
        try:
            conf = os.path.join(tmpdir, "Config.ini")
            hosts_dir = os.path.join(tmpdir, "Hosts")
            legacy_history = os.path.join(tmpdir, "History.ini")
            # A single-host Config.ini, as written by earlier versions
            write_sample_config(conf)
            legacy = ConfigManager(conf)
            legacy.WriteToConfig('Host', 'HostName', '10.1.2.3')
            legacy.WriteToConfig('Host', 'UserName', 'admin')
            legacy.WriteToConfig('Snoopy', 'FeedbackEmail', 'me@example.com')
            legacy.WriteToConfig('Backup', 'backupMUSDWH', 'E:/b.sh')
            ConfigManager(legacy_history).WriteToConfig('Backup', 'backupMUSDWH', '-v')
            before = [s for s in legacy.GetAllSections() if s not in ('Host', 'Snoopy', 'Hosts')]

            store = HostStore(conf, hosts_dir)
            name = store.MigrateLegacy(legacy_history)
            assert name == '10.1.2.3', name
            assert os.path.isfile(conf + '.bak') and not os.path.exists(legacy_history)
            assert store.MigrateLegacy(legacy_history) is None  # only once
            assert store.FeedbackEmail() == 'me@example.com'
            assert [n for n, _ in store.HostEntries()] == ['10.1.2.3']
            ctx = HostContext(*store.HostEntries()[0])
            ctx.Load()
            assert ctx.error is None and ctx.key == ('10.1.2.3', 'admin', 22), (ctx.error, ctx.key)
            assert ctx.Categories() == before, ctx.Categories()
            assert ctx.Tasks('Backup')[-1] == ('backupMUSDWH', 'E:/b.sh')
            assert ctx.GetHistory('Backup', 'backupMUSDWH') == '-v'  # history moved with the host
            assert set(ConfigManager(conf).GetAllSections()) == {'Snoopy', 'Hosts'}

            path = store.AddHost('Test Box', 'test.example', 'deploy', '2222')
            assert os.path.basename(path) == 'Test_Box.ini'
            store.AddHost('Test.Box', 'x', 'y', '22')  # different name, colliding file name
            assert [os.path.basename(p) for _, p in store.HostEntries()] == ['10.1.2.3.ini', 'Test_Box.ini', 'Test.Box.ini']
            store.RenameHost('Test Box', 'Staging')
            assert [n for n, _ in store.HostEntries()] == ['10.1.2.3', 'Staging', 'Test.Box']
            staging = HostContext(*store.HostEntries()[1])
            staging.Load()
            assert staging.key == ('test.example', 'deploy', 2222) and staging.Categories() == []
            store.RemoveHost('Staging')
            assert [n for n, _ in store.HostEntries()] == ['10.1.2.3', 'Test.Box']
            assert not os.path.exists(path) and any(f.startswith('Test_Box.ini.deleted-') for f in os.listdir(hosts_dir))

            broken = HostContext('Missing', os.path.join(hosts_dir, 'nope.ini'))
            broken.Load()
            assert broken.error and broken.Categories() == []
            print("HostStore: OK")
        finally:
            shutil.rmtree(tmpdir)

    @staticmethod
    def TestProcessManager():
        tmpdir = tempfile.mkdtemp()
        try:
            ok, output, log_file = ProcessManager.run_shell_command([sys.executable, "--version"], log_dir=tmpdir)
            assert ok and "Python" in output, output
            assert os.path.isfile(log_file)
            # Shell metacharacters must be passed as plain arguments, not executed.
            ok, output, _ = ProcessManager.run_shell_command(
                [sys.executable, "-c", "import sys; print(sys.argv[1])", "x & echo injected"], log_dir=tmpdir)
            assert ok and output.strip() == "x & echo injected", output
            ok, _, log_file = ProcessManager.run_shell_command(["no-such-command-snoopy"], log_dir=tmpdir)
            assert not ok and log_file is None
            print("ProcessManager: OK")
        finally:
            shutil.rmtree(tmpdir)

    @staticmethod
    def TestGUIManager():
        import wx
        from .GUIManager import GUIManager
        app = wx.App(False)
        gui = GUIManager()
        print(gui.AskFields(None, "Test Form", [{"name": "name", "label": "Name:"},
                                                {"name": "script", "label": "Script:", "kind": "file"},
                                                {"name": "flag", "label": "A checkbox", "kind": "check"}]))
        print(gui.PasswordGUI("Test password:", "Test Password"))
        print(gui.ConfirmGUI(None, "Test question?", "Test Confirm"))
        app.Destroy()


if __name__ == '__main__':
    TestApp.TestPaths()
    TestApp.TestCredentials()
    TestApp.TestConfigManager()
    TestApp.TestHostStore()
    TestApp.TestProcessManager()
    TestCommandBuilders()
    TestRemoteHost()
    TestLocalHost()
    TestRemoteScriptsWithRealBash()
    if "--gui" in sys.argv:
        TestApp.TestGUIManager()
