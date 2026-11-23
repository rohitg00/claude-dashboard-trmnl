from . import claude, codex, cursor, grok, pi
from .sqlite_agents import Hermes, OpenCode

ALL = {c.NAME: c for c in (claude, codex, grok, cursor, OpenCode, pi, Hermes)}
