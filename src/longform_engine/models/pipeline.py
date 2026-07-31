"""Optional local semantic model provider layer.

Semantic production commands require real local providers by default. Tests and
offline development can still opt into a deterministic local-hash profile.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import hashlib
import json
import math
import re

from longform_engine.config import ConfigDocument
from longform_engine.storage import atomic_write_text, resolve_project_root


@dataclass(frozen=True)
class ModelProfile:
    """A named embedding/reranker model profile."""

    name: str
    embedding_repo: str
    reranker_repo: str
    description: str
    local_only: bool = False


@dataclass(frozen=True)
class ModelInstallResult:
    """Result for an explicit model install/cache command."""

    profile: str
    models_dir: str
    manifest_file: str
    downloaded: bool
    embedding_path: str
    reranker_path: str
    warnings: tuple[str, ...]


class ModelError(ValueError):
    """Raised when a semantic command requires a real model that is not ready."""


@dataclass(frozen=True)
class ModelVerifyResult:
    """Result for checking local semantic model readiness."""

    ok: bool
    status: str
    profile: str
    models_dir: str
    embedding_model: str
    reranker_model: str
    embedding_cached: bool
    reranker_cached: bool
    embedding_loadable: bool
    reranker_loadable: bool
    provider_ready: bool
    download_required: bool
    can_auto_download: bool
    fallback_allowed: bool
    fallback_active: bool
    fallback: str
    warnings: tuple[str, ...]


PROFILES: dict[str, ModelProfile] = {
    "bge-m3": ModelProfile(
        name="bge-m3",
        embedding_repo="BAAI/bge-m3",
        reranker_repo="BAAI/bge-reranker-v2-m3",
        description="Default multilingual profile for offline Chinese long-form retrieval.",
    ),
    "qwen3": ModelProfile(
        name="qwen3",
        embedding_repo="Qwen/Qwen3-Embedding-8B",
        reranker_repo="Qwen/Qwen3-Reranker-0.6B",
        description="Higher-accuracy optional profile when local GPU resources are available.",
    ),
    "local-hash": ModelProfile(
        name="local-hash",
        embedding_repo="local/hash-embedding",
        reranker_repo="local/rule-reranker",
        description="Deterministic offline fallback used when no model is installed.",
        local_only=True,
    ),
}

MODEL_SNAPSHOT_IGNORE_PATTERNS = (
    "onnx/*",
    "openvino/*",
    "*.onnx",
)

CONCEPT_TERMS: dict[str, tuple[str, ...]] = {
    "forgiveness": ("原谅", "宽恕", "和解", "forgive", "forgiveness", "reconcile"),
    "rescue": ("救援", "救命", "搭救", "保护", "救下", "rescue", "save", "saved"),
    "concession": ("让步", "退让", "妥协", "道歉", "apology", "concession", "yield"),
    "betrayal": ("背叛", "出卖", "betray", "betrayal"),
    "conflict": ("冲突", "决裂", "争吵", "对抗", "conflict", "fight"),
    "foreshadow": ("伏笔", "线索", "预兆", "秘密", "clue", "secret"),
    "ability": ("能力", "代价", "冷却", "边界", "cost", "cooldown", "limit"),
    "location": ("地点", "转移", "离开", "抵达", "location", "arrive", "leave"),
}


def list_profiles() -> tuple[ModelProfile, ...]:
    """Return supported semantic model profiles."""

    return tuple(PROFILES.values())


def models_dir(config: ConfigDocument) -> Path:
    """Resolve the project-local semantic model cache directory."""

    root = resolve_project_root(config)
    semantic_config = config.data.get("semantic", {}) if isinstance(config.data.get("semantic"), dict) else {}
    configured = semantic_config.get("models_dir") or config.data.get("rag", {}).get("models_dir") or "70_runtime/models"
    path = Path(str(configured))
    return path if path.is_absolute() else root / path


def semantic_config(config: ConfigDocument) -> dict[str, Any]:
    configured = config.data.get("semantic")
    return configured if isinstance(configured, dict) else {}


def semantic_bool(config: ConfigDocument, key: str, default: bool) -> bool:
    value = semantic_config(config).get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def require_real_model(config: ConfigDocument) -> bool:
    return semantic_bool(config, "require_real_model", semantic_bool(config, "enabled", True))


def allow_fallback(config: ConfigDocument) -> bool:
    return semantic_bool(config, "allow_fallback", False) or not require_real_model(config)


def allow_network_download(config: ConfigDocument) -> bool:
    return semantic_bool(config, "allow_network_download", False)


def manifest_path(config: ConfigDocument) -> Path:
    return models_dir(config) / "semantic_models.json"


def selected_profile(config: ConfigDocument, requested: str | None = None) -> ModelProfile:
    semantic_config = config.data.get("semantic", {}) if isinstance(config.data.get("semantic"), dict) else {}
    rag_embedding = config.data.get("rag", {}).get("embedding") if isinstance(config.data.get("rag"), dict) else {}
    configured = None
    if isinstance(rag_embedding, dict):
        configured = rag_embedding.get("profile")
    configured = requested or configured or semantic_config.get("profile") or "bge-m3"
    return PROFILES.get(str(configured), PROFILES["bge-m3"])


def install_model_profile(
    config: ConfigDocument,
    *,
    profile: str = "bge-m3",
    download: bool = False,
) -> ModelInstallResult:
    """Create a local model manifest and optionally snapshot Hugging Face repos."""

    chosen = selected_profile(config, profile)
    cache_root = models_dir(config)
    embedding_path = cache_root / chosen.name / "embedding"
    reranker_path = cache_root / chosen.name / "reranker"
    embedding_path.mkdir(parents=True, exist_ok=True)
    reranker_path.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    downloaded = False
    if download and not chosen.local_only:
        try:
            from huggingface_hub import snapshot_download  # type: ignore

            snapshot_download(
                repo_id=chosen.embedding_repo,
                local_dir=embedding_path,
                ignore_patterns=MODEL_SNAPSHOT_IGNORE_PATTERNS,
            )
            snapshot_download(
                repo_id=chosen.reranker_repo,
                local_dir=reranker_path,
                ignore_patterns=MODEL_SNAPSHOT_IGNORE_PATTERNS,
            )
            downloaded = True
        except Exception as exc:  # pragma: no cover - depends on optional network/deps
            warnings.append(f"Hugging Face download skipped/failed: {exc}")
    elif download and chosen.local_only:
        warnings.append("local-hash profile does not require download.")

    payload = {
        "schema_version": 1,
        "active_profile": chosen.name,
        "profiles": {item.name: asdict(item) for item in PROFILES.values()},
        "installed": {
            chosen.name: {
                "embedding_repo": chosen.embedding_repo,
                "reranker_repo": chosen.reranker_repo,
                "embedding_path": str(embedding_path),
                "reranker_path": str(reranker_path),
                "downloaded": downloaded,
            }
        },
    }
    path = manifest_path(config)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return ModelInstallResult(
        profile=chosen.name,
        models_dir=str(cache_root),
        manifest_file=str(path),
        downloaded=downloaded,
        embedding_path=str(embedding_path),
        reranker_path=str(reranker_path),
        warnings=tuple(warnings),
    )


def verify_models(config: ConfigDocument) -> ModelVerifyResult:
    """Verify configured semantic models without silently activating fallback."""

    profile = selected_profile(config)
    path = manifest_path(config)
    manifest = read_json(path, default={})
    installed = manifest.get("installed") if isinstance(manifest, dict) else {}
    profile_info = installed.get(profile.name) if isinstance(installed, dict) else None
    embedding_path = Path(str(profile_info.get("embedding_path"))) if isinstance(profile_info, dict) and profile_info.get("embedding_path") else models_dir(config) / profile.name / "embedding"
    reranker_path = Path(str(profile_info.get("reranker_path"))) if isinstance(profile_info, dict) and profile_info.get("reranker_path") else models_dir(config) / profile.name / "reranker"

    embedding_cached = profile.local_only or directory_has_files(embedding_path)
    reranker_cached = profile.local_only or directory_has_files(reranker_path)
    warnings: list[str] = []
    if not path.exists() and not profile.local_only:
        warnings.append("semantic model manifest is missing.")
    if not embedding_cached:
        warnings.append("embedding model is not cached.")
    if not reranker_cached:
        warnings.append("reranker model is not cached.")

    embedding_loadable = profile.local_only or can_load_sentence_transformer(embedding_path)
    reranker_loadable = profile.local_only or can_load_sentence_transformer(reranker_path)
    if embedding_cached and not embedding_loadable:
        warnings.append("embedding model is cached but not loadable in this environment.")
    if reranker_cached and not reranker_loadable:
        warnings.append("reranker model is cached but not loadable in this environment.")

    fallback_allowed = allow_fallback(config) or profile.local_only
    provider_ready = (not profile.local_only) and embedding_loadable and reranker_loadable
    if provider_ready:
        status = "ready"
    elif fallback_allowed:
        status = "fallback_only"
    else:
        status = "download_required"
    download_required = status == "download_required"
    can_auto_download = download_required and allow_network_download(config) and not profile.local_only
    fallback_active = status == "fallback_only"
    fallback = "local-hash" if fallback_active else ""
    ok = status in {"ready", "fallback_only"}
    return ModelVerifyResult(
        ok=ok,
        status=status,
        profile=profile.name,
        models_dir=str(models_dir(config)),
        embedding_model=profile.embedding_repo,
        reranker_model=profile.reranker_repo,
        embedding_cached=embedding_cached,
        reranker_cached=reranker_cached,
        embedding_loadable=embedding_loadable,
        reranker_loadable=reranker_loadable,
        provider_ready=provider_ready,
        download_required=download_required,
        can_auto_download=can_auto_download,
        fallback_allowed=fallback_allowed,
        fallback_active=fallback_active,
        fallback=fallback,
        warnings=tuple(warnings),
    )


def ensure_models_ready(
    config: ConfigDocument,
    *,
    allow_download: bool = True,
    require_reranker: bool = True,
) -> ModelVerifyResult:
    """Return model status, optionally installing the configured profile first."""

    status = verify_models(config)
    if status.status == "ready" or status.status == "fallback_only":
        return status
    if not require_reranker and status.embedding_loadable and status.profile != "local-hash":
        return status
    if allow_download and status.can_auto_download:
        result = install_model_profile(config, profile=status.profile, download=True)
        if result.warnings:
            raise ModelError(
                "Semantic model download failed; run "
                f"`longform-engine models install project.yaml --profile {status.profile} --download`. "
                + " ".join(result.warnings)
            )
        status = verify_models(config)
        if status.status == "ready" or status.status == "fallback_only":
            return status
        if not require_reranker and status.embedding_loadable and status.profile != "local-hash":
            return status
    detail = " ".join(status.warnings)
    suffix = f" Details: {detail}" if detail else ""
    raise ModelError(
        "Semantic models are not ready "
        f"(status={status.status}, profile={status.profile}). Run "
        f"`longform-engine models install project.yaml --profile {status.profile} --download` "
        "or explicitly set `semantic.allow_fallback: true` for development fallback."
        f"{suffix}"
    )


def embed_text_with_provider(config: ConfigDocument, text: str, *, dims: int = 96) -> list[float]:
    """Embed text with a loadable local provider or explicit local-hash fallback."""

    status = verify_models(config)
    if status.embedding_loadable and status.profile != "local-hash":
        profile = selected_profile(config)
        path = models_dir(config) / profile.name / "embedding"
        vector = sentence_transformer_embed(path, text)
        if vector:
            return vector
    if status.fallback_allowed:
        return embed_text(text, dims=dims)
    raise ModelError(
        "Semantic embedding provider is not ready and fallback is disabled. Run "
        f"`longform-engine models install project.yaml --profile {status.profile} --download`."
    )


def rerank_pair(config: ConfigDocument, query: str, candidate: str, *, fallback_score: float = 0.0) -> float:
    """Return a rerank score with real reranker, embedding-only warning path, or explicit fallback."""

    status = verify_models(config)
    if status.reranker_loadable and status.profile != "local-hash":
        profile = selected_profile(config)
        path = models_dir(config) / profile.name / "reranker"
        score = sentence_transformer_rerank(path, query, candidate)
        if score is not None:
            return score
    if status.embedding_loadable and status.profile != "local-hash":
        query_vector = embed_text_with_provider(config, query)
        candidate_vector = embed_text_with_provider(config, candidate)
        semantic = cosine_similarity(query_vector, candidate_vector)
        return max(float(fallback_score), semantic)
    if not status.fallback_allowed:
        raise ModelError(
            "Semantic reranker is not ready and fallback is disabled. Run "
            f"`longform-engine models install project.yaml --profile {status.profile} --download`."
        )
    query_vector = embed_text(query)
    candidate_vector = embed_text(candidate)
    semantic = cosine_similarity(query_vector, candidate_vector)
    return max(float(fallback_score), semantic)


def embed_text(text: str, *, dims: int = 96) -> list[float]:
    """Return a deterministic lightweight semantic vector for offline operation."""

    vector = [0.0 for _ in range(dims)]
    expanded = semantic_expanded_terms(text)
    if not expanded:
        return vector
    for term in expanded:
        digest = hashlib.sha256(term.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        weight = 2.0 if term.startswith("concept:") else 1.0
        vector[bucket] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [round(value / norm, 8) for value in vector]


_MODEL_CACHE: dict[str, Any] = {}


def can_load_sentence_transformer(path: Path) -> bool:
    if not directory_has_files(path):
        return False
    try:
        import sentence_transformers  # type: ignore  # noqa: F401
    except Exception:
        return False
    return True


def sentence_transformer_embed(path: Path, text: str) -> list[float]:
    try:
        model = load_sentence_transformer(path)
        vector = model.encode([text], normalize_embeddings=True)[0]
        return [float(item) for item in vector]
    except Exception:
        return []


def sentence_transformer_rerank(path: Path, query: str, candidate: str) -> float | None:
    try:
        model = load_sentence_transformer(path)
        if hasattr(model, "predict"):
            value = model.predict([(query, candidate)])
            if isinstance(value, (list, tuple)):
                return float(value[0])
            return float(value)
        query_vector = model.encode([query], normalize_embeddings=True)[0]
        candidate_vector = model.encode([candidate], normalize_embeddings=True)[0]
        return cosine_similarity([float(item) for item in query_vector], [float(item) for item in candidate_vector])
    except Exception:
        return None


def load_sentence_transformer(path: Path) -> Any:
    key = str(path.resolve())
    if key not in _MODEL_CACHE:
        from sentence_transformers import SentenceTransformer  # type: ignore

        _MODEL_CACHE[key] = SentenceTransformer(str(path))
    return _MODEL_CACHE[key]


def cosine_similarity(left: list[float] | tuple[float, ...], right: list[float] | tuple[float, ...]) -> float:
    """Cosine similarity for already-materialized vectors."""

    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    dot = sum(float(left[index]) * float(right[index]) for index in range(size))
    left_norm = math.sqrt(sum(float(left[index]) ** 2 for index in range(size)))
    right_norm = math.sqrt(sum(float(right[index]) ** 2 for index in range(size)))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)


def semantic_expanded_terms(text: str) -> list[str]:
    """Extract terms and narrative concepts used by the offline fallback."""

    lowered = text.lower()
    terms: set[str] = set()
    for token in re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{1,}", lowered):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) >= 2:
                terms.add(token)
            if len(token) > 2:
                for index in range(0, len(token) - 1):
                    terms.add(token[index : index + 2])
        else:
            terms.add(token)

    active_concepts: set[str] = set()
    for concept, needles in CONCEPT_TERMS.items():
        if any(needle.lower() in lowered for needle in needles):
            active_concepts.add(concept)
            terms.add(f"concept:{concept}")

    # Narrative cause expansions for common long-form queries. This is not a
    # substitute for a real reranker; it gives offline tests a stable semantic
    # behavior and mirrors the intended model contract.
    if "forgiveness" in active_concepts:
        terms.update({"concept:rescue", "concept:concession", "concept:trust"})
    if "betrayal" in active_concepts:
        terms.update({"concept:conflict", "concept:relationship"})
    if "ability" in active_concepts:
        terms.update({"concept:cost", "concept:limit"})
    return sorted(terms)


def directory_has_files(path: Path) -> bool:
    return path.exists() and any(item.is_file() for item in path.rglob("*"))


def read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except json.JSONDecodeError:
        return default
