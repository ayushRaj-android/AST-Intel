"""Confidence scoring + conflict detection for Phase 4 signals.

The confidence model assigns each signal a base confidence per its
:class:`~ast_intel.history.models.SignalKind`, applies time-decay
based on how old the underlying citation is, and stacks small boosts
(or penalties) for type-specific signals — e.g. an *accepted* RFC is
worth more than a draft, a merged PR is worth more than a closed one.

The numbers are pinned in lock-step with the Phase 4 roadmap; tweak
them via :class:`ConfidenceConfig` if a team wants its own weights.
"""

from __future__ import annotations
