import os
from configparser import ConfigParser


def _new_parser():
    # No interpolation, so values containing '%' are read literally, and keep
    # keys like backupMUSDWH as written instead of lowercasing them.
    config = ConfigParser(interpolation=None)
    config.optionxform = str
    return config


class ConfigManager:

    def __init__(self, ConfigFile):
        self.ConfigFile = ConfigFile

    def get_ConfigFile(self):
        return self.ConfigFile

    def set_ConfigFile(self, ConfigFile):
        self.ConfigFile = ConfigFile

    def Load(self, missing_ok=False):
        """Return a ConfigParser for the config file.

        Raises FileNotFoundError if the file does not exist, unless missing_ok
        is set (ConfigParser.read would otherwise silently return an empty config).
        """
        config = _new_parser()
        if not os.path.isfile(self.ConfigFile):
            if missing_ok:
                return config
            raise FileNotFoundError(self.ConfigFile)
        config.read(self.ConfigFile, encoding="utf-8")
        return config

    def Save(self, config):
        folder = os.path.dirname(self.ConfigFile)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(self.ConfigFile, 'w', encoding="utf-8") as configfile:
            config.write(configfile)

    def WriteToConfig(self, Section, Parameter, Value):
        config = self.Load(missing_ok=True)
        if not config.has_section(Section):
            config.add_section(Section)
        config[Section][Parameter] = Value
        self.Save(config)

    def RemoveFromConfig(self, Section, Parameter):
        config = self.Load(missing_ok=True)
        if config.has_section(Section) and config.remove_option(Section, Parameter):
            self.Save(config)

    def AddSection(self, Section):
        config = self.Load(missing_ok=True)
        config.add_section(Section)  # raises DuplicateSectionError if present
        self.Save(config)

    def RemoveSection(self, Section):
        config = self.Load(missing_ok=True)
        if config.remove_section(Section):
            self.Save(config)

    def RenameSection(self, Old, New):
        """Rename a section, keeping its position and its keys."""
        config = self.Load(missing_ok=True)
        if not config.has_section(Old):
            return
        renamed = _new_parser()
        for section in config.sections():
            target = New if section == Old else section
            renamed.add_section(target)
            for key, value in config.items(section):
                renamed[target][key] = value
        self.Save(renamed)

    def RenameOption(self, Section, Old, New, Value):
        """Rename a key (and set its value), keeping its position in the section."""
        config = self.Load(missing_ok=True)
        if not config.has_section(Section):
            config.add_section(Section)
        items = config.items(Section)
        if Old not in dict(items):
            items.append((New, Value))
        config.remove_section(Section)
        config.add_section(Section)
        for key, value in items:
            if key == Old:
                key, value = New, Value
            config[Section][key] = value
        self.Save(config)

    def GetAllSections(self):
        return self.Load().sections()

    def GetAllSectionKeys(self, Section):
        return dict(self.Load().items(Section))

    def GetAllConfigKeys(self):
        config = self.Load()
        return {section: dict(config.items(section)) for section in config.sections()}

    def GetValue(self, Section, Key):
        return self.Load().get(Section, Key)
