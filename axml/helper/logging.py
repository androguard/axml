"""Logging
"""

from logging import basicConfig, getLogger, Logger

from rich.console import Console
from rich.logging import RichHandler

basicConfig(
    level='INFO',
    format="%(message)s",
    datefmt="[%Y-%m-%dT%H:%M:%S]",
    handlers=[RichHandler(console=Console(stderr=True))],
)

LOGGER: Logger = getLogger('axml')
