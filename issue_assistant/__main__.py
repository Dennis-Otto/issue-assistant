"""Runs the command line of the action: python3 -m issue_assistant COMMAND."""

import sys

from .cli import main

sys.exit(main())
