"""Five-tier memory architecture for persistent agent memory.

Split into focused modules:
- _entries.py — dataclasses for all memory entry types
- _stores.py — ShortTermMemory, EpisodicMemory, SemanticMemory, ProceduralMemory
- _pattern.py — PatternMemory (hybrid search, FTS5 + vector)
- _router.py — TieredRouter (cached → lightweight → full agent)
- _manager.py — MemoryManager facade (single point of contact)

Public API (same as before the split):
    from shared.memory_layer import MemoryManager, PatternMemory, TieredRouter
"""

from shared.memory_layer._entries import (  # noqa: F401
    EpisodicEntry,
    SemanticEntry,
    ProceduralEntry,
    ShortTermEntry,
    PatternEntry,
)
from shared.memory_layer._stores import (  # noqa: F401
    ShortTermMemory,
    EpisodicMemory,
    SemanticMemory,
    ProceduralMemory,
)
from shared.memory_layer._pattern import PatternMemory  # noqa: F401
from shared.memory_layer._router import TieredRouter  # noqa: F401
from shared.memory_layer._manager import (  # noqa: F401
    MemoryManager,
    get_shared_memory_manager,
    reset_shared_memory_manager,
)
