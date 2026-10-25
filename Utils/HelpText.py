welcome = """\
<html><body>
<h3>Welcome to Snoopy</h3>

<p>Snoopy organises your deployment scripts by host and category, and runs them
on those hosts over SSH.</p>

<p><b>Getting started</b></p>
<ol>
<li>Choose <i>File &gt; New Host</i> and enter the host's address, user name and password.</li>
<li>Select the host, add a category, then add tasks: <b>Browse...</b> picks each task's
    script on the host itself, and Snoopy remembers its path.</li>
<li>Pick a task, enter its parameters and press <b>Run</b> (F5).
    The output appears on that host's <i>Console</i> tab.</li>
</ol>

<p>Right-click the tree to add, edit or remove hosts, categories and tasks.
Connected hosts are shown in <b>bold</b>. Press F1 for help.</p>
</body></html>
"""

help_text = """\
<html><body>
<h3>Snoopy Help</h3>

<p><b>The window</b></p>
<ul>
<li><b>Left:</b> your hosts, each with its own categories and tasks. Connected
hosts are shown in bold. Type in the search box to filter by host, category,
task or script.</li>
<li><b>Details tab:</b> the selected host, category or task, with its actions.
For a task: its script, parameters and last run.</li>
<li><b>Console tab:</b> the selected host's live output, plus a command line for
ad-hoc commands on that host. Each host has its own console.</li>
<li><b>Remote Files tab:</b> browse, upload and download files on the selected host.</li>
<li><b>Status bar:</b> what Snoopy is doing, the selected host's connection, and
the selected script or address.</li>
</ul>

<p><b>Hosts</b></p>
<p>Add a host with <i>File &gt; New Host</i>; edit it by selecting it and choosing
<i>Edit</i>. Each host has a display name, an address, a user name and an SSH port.
Passwords are stored in Windows Credential Manager, never in the config files.
The first time Snoopy connects to a host it shows the host's key fingerprint;
only accept it if it matches the server. If the key later changes, Snoopy refuses
to connect.</p>
<p>Different hosts can run commands at the same time; each host runs one
command at a time.</p>

<p><b>Running on this PC</b></p>
<p>A host whose address is <tt>localhost</tt> or <tt>127.0.0.1</tt> does not use SSH:
there is no connection, password or host key. Its tasks and commands run on this
PC, and its Remote Files tab shows this PC's disk. The user name and port are not needed.</p>
<ul>
<li><tt>.cmd</tt> and <tt>.bat</tt> scripts run with cmd.exe, and <tt>.ps1</tt> scripts with
PowerShell, from the script's own folder (as if you double-clicked them).
<tt>pause</tt> does not wait.</li>
<li><tt>.sh</tt> scripts, console commands, grep and du run in bash (Git Bash if installed,
otherwise WSL).</li>
</ul>
<p>Windows scripts can only run on this PC; Snoopy refuses to send them to a remote host.</p>

<p><b>Running a task</b></p>
<p>Select a task, type its parameters on the Details tab and press <b>Run</b> (F5),
or double-click the task. The parameters you used last are filled in.</p>
<p>A task's script lives <b>on its host</b>: in the task dialog, <b>Browse...</b> lists
the host's files (over SFTP) so you can pick it, or type its full path (e.g.
<tt>/opt/scripts/backup.sh</tt> or <tt>~/bin/backup.sh</tt>). When you run the task,
Snoopy runs the script in place on the host, starting in the script's own folder.
If the file is executable its <tt>#!</tt> line is used, otherwise bash. Nothing
is copied to the host.</p>
<p><b>Stop</b> (Shift+F5) ends the selected host's run. To move a task to another
host or category, Edit it and pick a new <i>Host / category</i>.</p>

<p><b>Keyboard shortcuts</b></p>
<table cellpadding="2">
<tr><td>F5 / Shift+F5</td><td>Run the selected task / stop the selected host's command</td></tr>
<tr><td>Ctrl+N / Ctrl+Shift+N / Ctrl+Shift+H</td><td>New task / category / host</td></tr>
<tr><td>F2 / Delete</td><td>Edit / delete the selected item (in the tree)</td></tr>
<tr><td>Ctrl+F</td><td>Search</td></tr>
<tr><td>Ctrl+G / Ctrl+Shift+G</td><td>Grep / zgrep on the selected host</td></tr>
<tr><td>Ctrl+D</td><td>Disk usage on the selected host</td></tr>
<tr><td>Ctrl+1 / Ctrl+2 / Ctrl+3</td><td>Details / Console / Remote Files tab</td></tr>
<tr><td>F1</td><td>This help</td></tr>
</table>

<p><b>Files</b></p>
<p><tt>Conf/Config.ini</tt> lists the hosts; each host's settings and tasks are in
<tt>Conf/Hosts</tt>. Each run is written to the <tt>logs</tt> folder next to
Snoopy, named by date, host and task. Use <i>Open Logs</i> on the Console tab, or
<i>Open log</i> on the Details tab for a task's last run.</p>
</body></html>
"""
