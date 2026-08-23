"""Research inbox and canon promotion package."""

from .pipeline import (
    ImpactAnalysisResult,
    KnowledgeGapResult,
    ResearchError,
    ResearchItemResult,
    ResearchPromoteResult,
    add_research,
    detect_knowledge_gaps,
    impact_analyze,
    promote_research,
    search_research,
    search_web_candidates,
)

__all__ = [
    "ImpactAnalysisResult",
    "KnowledgeGapResult",
    "ResearchError",
    "ResearchItemResult",
    "ResearchPromoteResult",
    "add_research",
    "detect_knowledge_gaps",
    "impact_analyze",
    "promote_research",
    "search_research",
    "search_web_candidates",
]
