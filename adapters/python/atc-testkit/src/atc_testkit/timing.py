"""The time slack a test may add to its wall-clock bounds (docs/TESTKIT.md § Time slack)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import os

from .locations import Refusal

SLACK_VARIABLE = "ATC_TEST_TIME_SLACK"
DEFAULT = 2.0
MAXIMUM = 30.0
INVALID = "TIME_SLACK_INVALID"
INVALID_EXIT = 9
SOURCE_DEFAULT = "default"


@dataclass(frozen=True)
class Slack:
    """The slack in seconds and where it came from: the variable or the default."""
    seconds: float
    source: str

    def line(self) -> str:
        return f"time slack: {self.seconds:g} ({self.source})"


def parse(raw: str) -> float:
    """A slack setting: a finite number of seconds from 0 to ``MAXIMUM``; ``TIME_SLACK_INVALID`` otherwise."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = math.nan
    if not (math.isfinite(value) and 0 <= value <= MAXIMUM):
        raise Refusal(INVALID, f"{SLACK_VARIABLE}={raw!r}: give a number of seconds from 0 to {MAXIMUM:g}",
                      INVALID_EXIT)
    return value


def resolve(environ: Mapping[str, str] | None = None) -> Slack:
    """The variable when it is set and not empty, else ``DEFAULT``."""
    raw = (os.environ if environ is None else environ).get(SLACK_VARIABLE, "")
    return Slack(parse(raw), SLACK_VARIABLE) if raw != "" else Slack(DEFAULT, SOURCE_DEFAULT)


def slack(environ: Mapping[str, str] | None = None) -> float:
    """The seconds a test adds to a wall-clock bound; the call a project's tests make."""
    return resolve(environ).seconds
