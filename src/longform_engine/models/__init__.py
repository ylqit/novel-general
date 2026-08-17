"""Optional local semantic model management."""

from .pipeline import (
    ModelError,
    ModelInstallResult,
    ModelProfile,
    ModelVerifyResult,
    cosine_similarity,
    cache_kind,
    cache_status_payload,
    embed_text,
    embed_text_with_provider,
    ensure_models_ready,
    install_model_profile,
    list_profiles,
    models_dir,
    shared_model_cache_root,
    rerank_pair,
    verify_models,
)

__all__ = [
    "ModelError",
    "ModelInstallResult",
    "ModelProfile",
    "ModelVerifyResult",
    "cosine_similarity",
    "cache_kind",
    "cache_status_payload",
    "embed_text",
    "embed_text_with_provider",
    "ensure_models_ready",
    "install_model_profile",
    "list_profiles",
    "models_dir",
    "shared_model_cache_root",
    "rerank_pair",
    "verify_models",
]
