"""Compatibility facade for domain-specific Web Console helpers.

Routes and services historically resolve helpers through ``web_console``.
Keep this flat export surface while implementations live in ``helpers``.
"""

from .helpers.assistant import *  # noqa: F403
from .helpers.assistant import _module_available
from .helpers.documents import *  # noqa: F403
from .helpers.hardware import *  # noqa: F403
from .helpers.io import *  # noqa: F403
from .helpers.results import *  # noqa: F403
from .helpers.scene import *  # noqa: F403
from .helpers.security import *  # noqa: F403
from .helpers.tingwu import *  # noqa: F403
