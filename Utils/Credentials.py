import keyring
from keyring.errors import KeyringError, PasswordDeleteError

# Host passwords live in the OS credential store (Windows Credential Manager, the
# macOS Keychain, or the Secret Service on Linux desktops), keyed by host and user.
# Nothing secret is kept in source or in Config.ini.
#
# Without a credential store (e.g. a minimal Linux install with no keyring daemon),
# passwords are kept in memory for this session only, so Snoopy asks once per run.
SERVICE_PREFIX = "Snoopy"


class Credentials:

    _session = {}       # (service, username) -> password, used when there is no credential store
    persistent = True   # False once the credential store has been found unusable

    @staticmethod
    def _service(hostname):
        return SERVICE_PREFIX + ":" + hostname

    @staticmethod
    def get_password(hostname, username):
        key = (Credentials._service(hostname), username)
        if Credentials.persistent:
            try:
                return keyring.get_password(*key)
            except KeyringError:
                Credentials.persistent = False
        return Credentials._session.get(key)

    @staticmethod
    def set_password(hostname, username, password):
        key = (Credentials._service(hostname), username)
        if Credentials.persistent:
            try:
                keyring.set_password(key[0], key[1], password)
                return
            except KeyringError:
                Credentials.persistent = False
        Credentials._session[key] = password

    @staticmethod
    def delete_password(hostname, username):
        key = (Credentials._service(hostname), username)
        Credentials._session.pop(key, None)
        if Credentials.persistent:
            try:
                keyring.delete_password(*key)
            except PasswordDeleteError:
                pass
            except KeyringError:
                Credentials.persistent = False
