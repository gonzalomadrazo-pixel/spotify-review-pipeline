"""Stage interfaces: enricher, verifier, grouper, ranker, recommender."""
from abc import ABC, abstractmethod
from typing import Any, Optional
import json

from src.state import StateStore


class Stage(ABC):
    """Base stage interface."""

    def __init__(self, store: StateStore, config: dict):
        self.store = store
        self.config = config

    @abstractmethod
    def run(self, batch: list[dict]) -> list[dict]:
        """Process a batch, return results."""
        pass


class Enricher(Stage):
    """Classify reviews using model."""

    def __init__(self, store: StateStore, config: dict):
        super().__init__(store, config)
        self.model = config.get("model", "claude-haiku-4-5-20251001")
        self.label_config = config.get("label_config", "enrich_v1")

    def run(self, batch: list[dict]) -> list[dict]:
        """Enrich batch. Return list of {k, result_or_error}."""
        # This is a stub; the real implementation calls a model.
        # For now, return empty results to test the pipeline structure.
        return []


class Verifier(Stage):
    """Independent re-labeling for a sample."""

    def run(self, batch: list[dict]) -> list[dict]:
        """Verify batch. Return list of {k, topic, intent, severity, confident}."""
        return []


class Grouper(Stage):
    """Suggest issue names and coherence for clusters."""

    def run(self, issues: list[dict]) -> list[dict]:
        """Group reviews into issues. Return list of {issue_id, name, description}."""
        return []


class Ranker(Stage):
    """Compute issue ranking from complaint/cancellation membership."""

    def __init__(self, store: StateStore, config: dict):
        super().__init__(store, config)

    def run(self) -> dict:
        """Compute ranking. Return {issues: [...], by_area: {...}}."""
        # Stub
        return {"issues": [], "by_area": {}}


class Recommender(Stage):
    """Write decision memo from aggregates and evidence."""

    def run(self, aggregates: dict, evidence: dict) -> str:
        """Generate memo. Return markdown."""
        # Stub
        return "# Recommendation\n\n(Pending model call.)"
