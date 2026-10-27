"""USB Mouse Fusion open API v1 Python SDK."""
from .client import FusionClient
from .errors import FusionError

__all__ = ['FusionClient', 'FusionError']
__version__ = '0.2.0'

