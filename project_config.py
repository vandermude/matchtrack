"""
project_config.py - shared configuration for the matchtrack codebase
Declares global parameters used across the codebase. The matchtrack tools take
their SQLite index path as a command-line argument, so the only shared global
here is the Anthropic model id. Import by adding this directory to sys.path and
doing: from project_config import ANTHROPIC_MODEL.
=====================
INPUT ARGS:
(none - this module is imported, not run from the command line)
"""


ANTHROPIC_MODEL = 'claude-opus-4-8'
