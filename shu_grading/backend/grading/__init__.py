from .base import Grader, GraderRegistry, GradingInput
from .essay import EssayGrader


def default_registry(provider_factory=None):
    registry = GraderRegistry()
    registry.register(EssayGrader(provider_factory))
    return registry
