import os
import shlex
import logging
import subprocess
from time import localtime, strftime

from .Paths import LOG_DIR


class ProcessManager:

    @staticmethod
    def run_shell_command(command_line, log_dir=LOG_DIR):
        """Run a command without a shell and log its output.

        command_line may be a list of arguments (preferred) or a string, which
        is split into arguments. Running without a shell means user input
        cannot inject extra commands. Returns (success, output, log_file).
        """
        if isinstance(command_line, str):
            command_line_args = shlex.split(command_line, posix=(os.name != 'nt'))
        else:
            command_line_args = list(command_line)

        logging.info('Subprocess: %s', command_line_args)

        try:
            result = subprocess.run(
                command_line_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as exception:
            logging.error('Subprocess failed: %s', exception)
            return False, str(exception), None

        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, strftime("%Y-%m-%d_%H-%M-%S", localtime()) + ".log")
        with open(log_file, "w", encoding="utf-8") as text_file:
            text_file.write("$ " + subprocess.list2cmdline(command_line_args) + "\n")
            text_file.write(result.stdout or "")
            text_file.write("\n[exit code %d]\n" % result.returncode)

        logging.info('Subprocess finished with exit code %d', result.returncode)
        return result.returncode == 0, result.stdout, log_file
