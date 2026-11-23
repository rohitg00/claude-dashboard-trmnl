from . import claude, codex, grok, pi
from .sqlite_agents import Hermes, OpenCode

ALL = {c.NAME: c for c in (claude, codex, grok, OpenCode, pi, Hermes)}
