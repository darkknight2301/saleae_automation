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
expanding to a bare, unquoted path (e.g. {{device0}} -> /dev/nvme0).
"""

import json
import os
import re

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


class VariableError(Exception):
    """Raised when a .nvtest file references a variable that isn't loaded."""
    pass


class VariableManager:
    def __init__(self, variables_file: str = None):
        self.variables_file = variables_file
        self._values = {}
        if variables_file:
            self.load(variables_file)

    def load(self, variables_file: str):
        if not os.path.exists(variables_file):
            raise FileNotFoundError(f"Variables file not found: {variables_file}")
        with open(variables_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"Variables file must contain a JSON object: {variables_file}")
        self._values = data
        self.variables_file = variables_file

    def get(self, name: str):
        if name not in self._values:
            raise VariableError(f"Unknown variable {{{{{name}}}}} (not found in {self.variables_file})")
        return self._values[name]

    def substitute(self, text: str) -> str:
        """Replace every {{name}} in `text` with its loaded value (as str).
        Raises VariableError on the first unknown name."""
        if text is None or "{{" not in text:
            return text

        def _replace(match):
            return str(self.get(match.group(1)))

        return _PLACEHOLDER_RE.sub(_replace, text)
