import os

# Resolve everything relative to the application folder, not the current
# working directory, so Snoopy can be launched from anywhere.
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_FILE = os.path.join(APP_DIR, "Conf", "Config.ini")
HOSTS_DIR = os.path.join(APP_DIR, "Conf", "Hosts")
# Single-host history file from before multi-host support; moved on migration.
HISTORY_FILE = os.path.join(APP_DIR, "Conf", "History.ini")
IMAGES_DIR = os.path.join(APP_DIR, "images")
LOG_DIR = os.path.join(APP_DIR, "logs")

# Per-user data (trusted SSH host keys) lives outside the app folder:
# %APPDATA%\Snoopy on Windows, ~/.config/snoopy (or $XDG_CONFIG_HOME/snoopy) elsewhere.
if os.name == "nt":
    USER_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Snoopy")
else:
    USER_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "snoopy")
KNOWN_HOSTS_FILE = os.path.join(USER_DIR, "known_hosts")


def image(name):
    return os.path.join(IMAGES_DIR, name)
