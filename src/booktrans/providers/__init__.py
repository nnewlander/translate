"""Translation candidate providers.

Providers are deliberately outside the Harness and production control plane:
they return untrusted candidates only.
"""

from .translation_provider import (
    ApprovedTranslationExample,
    ProviderConfigurationError,
    ProviderContractGap,
    ProviderRuntimeError,
    TranslationCandidate,
    TranslationProvider,
    TranslationProviderRequest,
)
from .qwen_max import (
    ApprovedRepairContract,
    QwenMaxProvider,
    QwenMaxRepairProvider,
    QwenMaxReviewerProvider,
    ReviewResult,
    ReviewerProviderResult,
    StructuredProviderResult,
    StructuredTranslationCandidate,
    apply_narrow_repair,
    structured_candidate_to_raw,
)
from .router import RouteDecision, route_semantic_unit
from .failures import classify_provider_exception
from .repair_gate import decide_approved_repair_contract

__all__ = [
    "ApprovedTranslationExample",
    "ProviderConfigurationError",
    "ProviderContractGap",
    "ProviderRuntimeError",
    "TranslationCandidate",
    "TranslationProvider",
    "TranslationProviderRequest",
    "ApprovedRepairContract",
    "QwenMaxProvider",
    "QwenMaxRepairProvider",
    "QwenMaxReviewerProvider",
    "ReviewResult",
    "ReviewerProviderResult",
    "StructuredProviderResult",
    "StructuredTranslationCandidate",
    "apply_narrow_repair",
    "structured_candidate_to_raw",
    "RouteDecision",
    "route_semantic_unit",
    "classify_provider_exception",
    "decide_approved_repair_contract",
]
