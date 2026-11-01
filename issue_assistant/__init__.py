"""Issue assistant: labels, a first analysis, duplicates and unanswered questions.

The GitHub Action in action.yml runs this package, with `python3 -m issue_assistant`,
in the repository it looks after. That repository keeps its labels in
.github/labels.toml, a description of the project and its settings in
.github/issue-assistant/, and copies of the four workflows and the firewall policy,
which install writes from templates/.github/; see README.md.

An AI engine from ENGINES writes the analysis. It only reads: the checkout and a
context folder that `context` writes, with the prompt and the JSON schema of its
answer. The answer reaches GitHub only through `apply`, which checks it against the
schema, the labels and the issues that exist, and which runs in a job without the
engine. Reminders, closing, reopening and the labels of the issue forms work
without one.

docs/architecture/modules.md has the map of the modules. Each imports only from
those above it in the list below, so the list reads from the ground up.
"""

# The tests, the fuzzers and the scripts reach every name through the package, as
# `assistant.render_analysis`; only the code of a module uses its imports by name.
from .paths import *
from .constants import *
from .patterns import *
from .values import *
from .errors import *
from .languages import *
from .engines import *
from .settings import *
from .fixture import *
from .github import *
from .issues import *
from .cleaning import *
from .answer import *
from .comments import *
from .applying import *
from .learning import *
from .digests import *
from .context import *
from .commands import *
from .events import *
from .sweeps import *
from .labels import *
from .code_scanning import *
from .actionlint import *
from .setup_check import *
from .installation import *
from .running import *
from .evaluation import *
from .accuracy import *
from .cli import *
