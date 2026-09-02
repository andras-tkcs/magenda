"""The one exception type every magenda tool call can raise -- caught at the
MCP boundary (server.py) and turned into a tool error instead of a crash.

Split into its own module (rather than living on whichever module happens to
need it first, as it used to on xml_ops.py) so it has no dependents of its
own: everything from agenda_state.py to the compiler's xml_ops.py can import
it without dragging in lxml, pymupdf, or anything else.
"""
from __future__ import annotations


class MagendaError(Exception):
    """Raised for any user-facing failure: bad input, capacity exceeded,
    template/compiled-bundle problems. Caught at the MCP tool boundary."""
