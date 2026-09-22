"""HLMemo core: pure, DB-free building blocks (normalisation, metering, chunking, embedding, clues).

Version constants are stored alongside indexed data so that a change in any of
them can be detected and re-indexed (PHASE0-SPEC §4 step 7: vectors are filtered
by the current ``model@revision/preproc``).

This module deliberately imports no submodule: submodules import these constants
from the package, so keep it dependency-free.
"""

NORMALIZER_VERSION: int = 1
CHUNKER_VERSION: int = 1
METER_VERSION: str = "o200k_base"

MODEL_ID: str = "intfloat/multilingual-e5-small"
MODEL_REVISION: str = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
EMBEDDING_DIMS: int = 384
EMBEDDER_VERSION: str = f"{MODEL_ID}@{MODEL_REVISION}"

__all__ = [
    "NORMALIZER_VERSION",
    "CHUNKER_VERSION",
    "METER_VERSION",
    "MODEL_ID",
    "MODEL_REVISION",
    "EMBEDDING_DIMS",
    "EMBEDDER_VERSION",
]
