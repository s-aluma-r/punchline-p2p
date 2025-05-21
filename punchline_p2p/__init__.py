"""
.. include:: ../README.md
"""
from .punchline_client import PunchlineClient

try:
    from .punchline_server import PunchlineServer  # only import PunchlineServer directly if pandas is installed and therefore dont get an error
except (ImportError, ModuleNotFoundError):
    def PunchlineServer():
        raise ImportError("PunchlineServer requires optional dependencies. Install them using 'pip install punchline_p2p[server]'.")
