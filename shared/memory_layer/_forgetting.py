"""ForgettingEngine — 6-signal decay, lifecycle promotion/demotion, revival."""

import math
from datetime import datetime
from typing import Optional

from shared.logging_config import get_logger
from shared.memory_layer._entries import (
    MemoryEntry, Lifecycle, ProtectionLevel,
)

logger = get_logger(__name__)

BASE_STABILITY = 48.0

# Thresholds
STM_THRESHOLD = 0.4     # STM tombstoned when decay falls below this
MTM_THRESHOLD = 0.1     # MTM tombstoned when decay falls below this
LTM_COLD_DECAY = 0.25   # LTM demoted to COLD when decay falls below this

# Promotion thresholds
STM_TO_MTM_ACCESSES = 3
MTM_TO_LTM_ACCESSES = 10
MTM_TO_LTM_VALIDATIONS = 5


class ForgettingEngine:
    def __init__(self, neo4j=None):
        self._neo4j = neo4j

    def compute_decay(self, entry: MemoryEntry) -> float:
        """Compute decay score 0.0-1.0 from 6 signals.

        Core signals (always present, weights sum to 1.0):
          recency (0.35) + frequency (0.30) + quality (0.20) + uniqueness (0.15)

        Graph bonus signals (additive, up to +0.10 each, capped at 1.0):
          connectivity bonus: +0.10 * min(1, degree/5)  when degree > 0
          impact bonus:       +0.10 * downstream/10     when downstream > 0

        Omitting graph signals when no data avoids penalising brand-new entries.
        """
        hours_since = max(0.001, (datetime.now() - entry.last_accessed).total_seconds() / 3600.0)

        # Recency: exponential decay with access-stabilised half-life
        stability = BASE_STABILITY * (1.0 + 0.3 * entry.access_count)
        recency = math.exp(-hours_since / stability)

        # Frequency: brand-new entry treated as high-frequency; older entries scored by rate
        if hours_since < 1.0:
            frequency = 1.0
        else:
            days_since = hours_since / 24.0
            frequency = min(1.0, entry.access_count / max(1.0, days_since))

        # Quality: normalised so score 7+ = 1.0 (7 is the "good quality" baseline)
        quality = min(1.0, entry.score / 7.0)

        # Uniqueness: last-survivor gets full score, redundant entries are discounted
        similar = self._neo4j.count_similar(entry.memory_id) if self._neo4j else 0
        if similar == 0:
            uniqueness = 1.0
        elif similar <= 2:
            uniqueness = 0.7
        else:
            uniqueness = 0.3

        # Core weighted sum
        score = (
            recency * 0.35
            + frequency * 0.30
            + quality * 0.20
            + uniqueness * 0.15
        )

        # Connectivity bonus (only when graph data exists)
        edge_count = self._neo4j.degree(entry.memory_id) if self._neo4j else 0
        if edge_count > 0:
            connectivity = min(1.0, edge_count / 5.0)
            score = min(1.0, score + connectivity * 0.10)

        # Impact bonus (only when downstream scores exist)
        downstream = self._neo4j.avg_downstream_score(entry.memory_id) if self._neo4j else 0.0
        if downstream:
            impact = downstream / 10.0
            score = min(1.0, score + impact * 0.10)

        return score

    def get_protection(self, entry: MemoryEntry) -> ProtectionLevel:
        """Determine protection level for an entry."""
        if entry.payload.get("pinned"):
            return ProtectionLevel.PINNED

        # Last-survivor: the only copy of a piece of knowledge gets protection.
        # (For STM this is advisory — evaluate_single still permits expiry.)
        similar = self._neo4j.count_similar(entry.memory_id) if self._neo4j else 1
        if similar == 0:
            return ProtectionLevel.PROTECTED

        if entry.confidence >= 0.95 and entry.lifecycle == Lifecycle.LTM:
            return ProtectionLevel.PROTECTED

        # Hub-node protection: highly-connected entries are harder to remove
        degree = self._neo4j.degree(entry.memory_id) if self._neo4j else 0
        if degree >= 5:
            return ProtectionLevel.ELEVATED

        return ProtectionLevel.NONE

    def evaluate_single(self, entry: MemoryEntry) -> dict:
        """Evaluate a single entry and return recommended actions."""
        actions: dict = {}
        decay = self.compute_decay(entry)
        actions["decay_score"] = decay
        protection = self.get_protection(entry)

        # Promotion checks (run first — a promotable entry is never tombstoned)
        if entry.lifecycle == Lifecycle.STM and entry.access_count >= STM_TO_MTM_ACCESSES:
            hours_since_creation = (datetime.now() - entry.created_at).total_seconds() / 3600.0
            if hours_since_creation <= 24:
                actions["promote_to"] = Lifecycle.MTM
                return actions

        if entry.lifecycle == Lifecycle.MTM and entry.access_count >= MTM_TO_LTM_ACCESSES:
            validations = entry.payload.get("times_validated", entry.payload.get("times_used", 0))
            if validations >= MTM_TO_LTM_VALIDATIONS:
                actions["promote_to"] = Lifecycle.LTM
                return actions

        # Protection blocks tombstoning and demotion for durable tiers.
        # STM entries can still expire even when last_survivor — they are
        # ephemeral by nature; the knowledge must be re-learned if valuable.
        if protection == ProtectionLevel.PINNED:
            return actions
        if protection == ProtectionLevel.PROTECTED and entry.lifecycle != Lifecycle.STM:
            return actions

        # Tombstone checks for STM / MTM
        threshold = {
            Lifecycle.STM: STM_THRESHOLD,
            Lifecycle.MTM: MTM_THRESHOLD,
        }.get(entry.lifecycle)

        if threshold is not None:
            if protection == ProtectionLevel.ELEVATED:
                threshold *= 0.5
            if decay < threshold:
                actions["tombstone"] = True
                return actions

        # LTM demotion to Cold (only when not ELEVATED)
        if entry.lifecycle == Lifecycle.LTM and protection != ProtectionLevel.ELEVATED:
            if decay < LTM_COLD_DECAY:
                actions["demote_to"] = Lifecycle.COLD

        return actions
