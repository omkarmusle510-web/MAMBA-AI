"""Mamba's browser-interaction capability.

Provider-independent browser control behind Mamba's existing boundaries:

::

    Planner -> TaskExecutor -> BrowserTaskHandler -> BrowserTool
        -> BrowserSession (target binding)
            -> BrowserProvider adapter
                -> Playwright MCP -> Chrome
    -> Observation -> existing Verifier -> Memory/Response

Nothing here plans, routes models, evaluates permissions, or verifies outcomes:
those stay in ``core/``, ``permissions/``, and ``verification/``. The capability
only resolves a target safely, performs one browser operation, and returns a
structured observation.
"""

from .session import (
    BrowserBinding,
    BrowserSession,
    create_default_provider,
    normalize_url,
    same_page,
)
from .tool import (
    BROWSER_CAPABILITY_ID,
    BROWSER_OPERATIONS,
    BROWSER_TOOL_NAME,
    BrowserOperationDefinition,
    BrowserTool,
    BrowserToolHandler,
    browser_operation_metadata,
    is_consequential,
)
from .types import (
    CONSEQUENTIAL_CAPABLE_ACTIONS,
    MUTATING_ACTIONS,
    READ_ONLY_ACTIONS,
    SCROLL_DIRECTIONS,
    BrowserAction,
    BrowserElement,
    BrowserElementError,
    BrowserError,
    BrowserOutcome,
    BrowserPage,
    BrowserProvider,
    BrowserTarget,
    BrowserTargetError,
)

__all__ = [
    "BROWSER_CAPABILITY_ID",
    "BROWSER_OPERATIONS",
    "BROWSER_TOOL_NAME",
    "BrowserAction",
    "BrowserBinding",
    "BrowserElement",
    "BrowserElementError",
    "BrowserError",
    "BrowserOperationDefinition",
    "BrowserOutcome",
    "BrowserPage",
    "BrowserProvider",
    "BrowserSession",
    "BrowserTarget",
    "BrowserTargetError",
    "BrowserTool",
    "BrowserToolHandler",
    "CONSEQUENTIAL_CAPABLE_ACTIONS",
    "MUTATING_ACTIONS",
    "READ_ONLY_ACTIONS",
    "SCROLL_DIRECTIONS",
    "browser_operation_metadata",
    "create_default_provider",
    "is_consequential",
    "normalize_url",
    "same_page",
]
