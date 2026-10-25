"""Hosts and their configuration files.

Conf/Config.ini is the global file:

    [Snoopy]
    FeedbackEmail = someone@example.com

    [Hosts]
    Production = Hosts/Production.ini
    Test = Hosts/Test.ini

Each host file holds the connection details and that host's categories of tasks
(the same layout Config.ini had when Snoopy supported a single host):

    [Host]
    HostName = 10.0.0.1
    UserName = admin
    Port = 22

    [Backup]
    backup = E:/path/to/backup.sh

Remembered parameters, last runs and tool inputs live next to it in
<host file>.history.ini.
"""
import configparser
import os
import re
import shutil
from time import localtime, strftime

from .ConfigManager import ConfigManager
from .LocalHost import is_local

HOST_SECTION = "Host"
APP_SECTION = "Snoopy"
HOSTS_SECTION = "Hosts"
RESERVED_SECTIONS = {HOST_SECTION.lower(), APP_SECTION.lower(), HOSTS_SECTION.lower()}
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\- ]+$")


class HostContext:
    """One host: its config, history and (set by the window) connection, job and panels."""

    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.manager = ConfigManager(path)
        self.history = ConfigManager(os.path.splitext(path)[0] + ".history.ini")
        self.config = None
        self.error = None
        self.hostname = ""
        self.username = ""
        self.port = 22
        self.remote = None
        self.job = None
        self.console = None
        self.files = None

    def Load(self):
        """Read the host file. On failure, sets self.error and leaves no categories."""
        self.config, self.error = None, None
        try:
            config = self.manager.Load()
        except FileNotFoundError:
            self.error = "Host file not found: " + self.path
            return
        except configparser.Error as exception:
            self.error = "Host file could not be read: " + str(exception)
            return
        self.config = config
        self.hostname = config.get(HOST_SECTION, "HostName", fallback="").strip()
        self.username = config.get(HOST_SECTION, "UserName", fallback="").strip()
        try:
            self.port = int(config.get(HOST_SECTION, "Port", fallback="22"))
        except ValueError:
            self.port = 22

    @property
    def key(self):
        return (self.hostname, self.username, self.port)

    @property
    def address(self):
        if not self.hostname:
            return "not set"
        if is_local(self.hostname):
            return "this PC (%s)" % self.hostname
        port = "" if self.port == 22 else ":%d" % self.port
        return "%s@%s%s" % (self.username, self.hostname, port)

    def IsConnected(self):
        return bool(self.remote and self.remote.is_connected())

    def Categories(self):
        if self.config is None:
            return []
        return [s for s in self.config.sections() if s.lower() not in RESERVED_SECTIONS]

    def Tasks(self, category):
        return self.config.items(category) if self.config and self.config.has_section(category) else []

    def GetHistory(self, section, key, default=""):
        try:
            return self.history.Load(missing_ok=True).get(section, key, fallback=default)
        except configparser.Error:
            return default

    def SetHistory(self, section, key, value):
        try:
            self.history.WriteToConfig(section, key, value)
        except (OSError, configparser.Error):
            pass


class HostStore:
    """The global config file and the list of host files it points to."""

    def __init__(self, conf_file, hosts_dir):
        self.global_manager = ConfigManager(conf_file)
        self.conf_dir = os.path.dirname(conf_file)
        self.hosts_dir = hosts_dir

    def Load(self):
        return self.global_manager.Load(missing_ok=True)

    def HostEntries(self):
        """Return [(name, absolute host file path)] in config order."""
        config = self.Load()
        if not config.has_section(HOSTS_SECTION):
            return []
        return [(name, os.path.normpath(os.path.join(self.conf_dir, value)))
                for name, value in config.items(HOSTS_SECTION)]

    def FeedbackEmail(self):
        return self.Load().get(APP_SECTION, "FeedbackEmail", fallback="").strip()

    def SetFeedbackEmail(self, email):
        self.global_manager.WriteToConfig(APP_SECTION, "FeedbackEmail", email)

    def _unique_file(self, name):
        base = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "host"
        candidate, n = base, 2
        while os.path.exists(os.path.join(self.hosts_dir, candidate + ".ini")) or \
                os.path.exists(os.path.join(self.hosts_dir, candidate + ".history.ini")):
            candidate, n = "%s_%d" % (base, n), n + 1
        return os.path.join(self.hosts_dir, candidate + ".ini")

    def _relative(self, path):
        return os.path.relpath(path, self.conf_dir).replace("\\", "/")

    def AddHost(self, name, hostname, username, port):
        """Create a host file and list it in the global config. Returns its path."""
        os.makedirs(self.hosts_dir, exist_ok=True)
        path = self._unique_file(name)
        host = ConfigManager(path)
        host.WriteToConfig(HOST_SECTION, "HostName", hostname)
        host.WriteToConfig(HOST_SECTION, "UserName", username)
        host.WriteToConfig(HOST_SECTION, "Port", str(port))
        self.global_manager.WriteToConfig(HOSTS_SECTION, name, self._relative(path))
        return path

    def RenameHost(self, old, new):
        config = self.Load()
        value = config.get(HOSTS_SECTION, old)
        self.global_manager.RenameOption(HOSTS_SECTION, old, new, value)

    def RemoveHost(self, name):
        """Remove a host from the list. Its files are kept, renamed to *.deleted-<time>."""
        path = dict(self.HostEntries()).get(name)
        self.global_manager.RemoveFromConfig(HOSTS_SECTION, name)
        if path:
            suffix = ".deleted-" + strftime("%Y%m%d-%H%M%S", localtime())
            for file in (path, os.path.splitext(path)[0] + ".history.ini"):
                if os.path.isfile(file):
                    os.replace(file, file + suffix)

    def MigrateLegacy(self, legacy_history=None):
        """Convert a single-host Config.ini ([Host] + categories) to the multi-host layout.

        The old file is kept as Config.ini.bak. Returns the new host's name, or None
        if there was nothing to migrate.
        """
        config = self.global_manager.Load(missing_ok=True)
        if not config.has_section(HOST_SECTION):
            return None
        hostname = config.get(HOST_SECTION, "HostName", fallback="").strip()
        name = re.sub(r"[^A-Za-z0-9_.\- ]+", "_", hostname).strip() or "Host"
        os.makedirs(self.hosts_dir, exist_ok=True)
        path = self._unique_file(name)

        host_config = ConfigManager(path).Load(missing_ok=True)
        new_global = ConfigManager(self.global_manager.get_ConfigFile()).Load(missing_ok=True)
        for section in [s for s in new_global.sections()]:
            new_global.remove_section(section)
        for section in config.sections():
            if section.lower() in (APP_SECTION.lower(), HOSTS_SECTION.lower()):
                target = new_global
            else:
                target = host_config
            target.add_section(section)
            for key, value in config.items(section):
                target[section][key] = value
        if not new_global.has_section(HOSTS_SECTION):
            new_global.add_section(HOSTS_SECTION)
        new_global[HOSTS_SECTION][name] = self._relative(path)

        conf_file = self.global_manager.get_ConfigFile()
        backup = conf_file + ".bak"
        n = 2
        while os.path.exists(backup):
            backup, n = "%s.bak%d" % (conf_file, n), n + 1
        shutil.copy2(conf_file, backup)
        ConfigManager(path).Save(host_config)
        self.global_manager.Save(new_global)

        if legacy_history and os.path.isfile(legacy_history):
            os.replace(legacy_history, os.path.splitext(path)[0] + ".history.ini")
        return name
