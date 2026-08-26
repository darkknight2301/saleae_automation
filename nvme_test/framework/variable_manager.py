"""
variable_manager.py - VariableManager: load/get/substitute common variables.

Syntax: {{variable_name}} in RUN commands and EXPECT values. Double-curly
was chosen (not ${...}) because RUN strings go through shell=True, and
${...} is live POSIX shell syntax -- {{...}} has no shell meaning, so it
never collides with a test writer's legitimate shell command. No
conditionals/loops -- one substitution pass per known variable, per the
"not a templating engine" rule.

Trust boundary (review finding F-9): substituted values are inserted
verbatim into RUN strings that are then executed via shell=True. This is
safe as long as the variables file (common_variables.json, or whatever
--config points the framework at) is authored/controlled by the same
person who writes the .nvtest files -- exactly like the .nvtest files
themselves, it must be treated as trusted input, not sanitized untrusted
data. Do not populate this file from a source outside the test author's
control (e.g. injected from a ticket field or an external system) without
first validating its values; a value containing shell metacharacters
(`, $(), ;, |) would execute as part of the RUN command. Escaping every
substituted value automatically (e.g. shlex.quote()) was considered and
rejected: it would break the common, legitimate case of a variable
expanding to a bare, unquoted path (e.g. {{device}} -> /dev/nvme0).

Runtime capture (RUN ... CAPTURE <name>): in addition to variables loaded
once from a JSON file, TestRunner calls set() to store a RUN's captured
stdout as a variable, at runtime, mid-test. get()/set()/substitute() are
lock-protected so a PARALLEL block (concurrent RUNs, each possibly
capturing into or substituting from the same VariableManager) can't race
on the underlying dict.
"""

import json
import os
import re
import threading

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


class VariableError(Exception):
    """Raised when a .nvtest file references a variable that isn't loaded."""
    pass


class VariableManager:
    def __init__(self, variables_file: str = None):
        self.variables_file = variables_file
        self._values = {}
        self._lock = threading.Lock()
        if variables_file:
            self.load(variables_file)

    def load(self, variables_file: str):
        if not os.path.exists(variables_file):
            raise FileNotFoundError(f"Variables file not found: {variables_file}")
        with open(variables_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"Variables file must contain a JSON object: {variables_file}")
        with self._lock:
            self._values = data
        self.variables_file = variables_file

    def get(self, name: str):
        with self._lock:
            if name not in self._values:
                raise VariableError(f"Unknown variable {{{{{name}}}}} (not found in {self.variables_file})")
            return self._values[name]

    def set(self, name: str, value):
        """Store or overwrite a variable at runtime (e.g. RUN ... CAPTURE
        <name>). Takes precedence over any same-named value loaded from
        the JSON variables file for the rest of this run."""
        with self._lock:
            self._values[name] = value

    def substitute(self, text: str) -> str:
        """Replace every {{name}} in `text` with its loaded value (as str).
        Raises VariableError on the first unknown name."""
        if text is None or "{{" not in text:
            return text

        def _replace(match):
            return str(self.get(match.group(1)))

        return _PLACEHOLDER_RE.sub(_replace, text)
