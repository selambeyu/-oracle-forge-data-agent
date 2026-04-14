"""
Self-correction scaffold for the Oracle Forge runtime.

The real implementation will classify execution failures and produce targeted
plan revisions. For now this module only owns the retry decision boundary.

TODO:
- Add failure classification
- Add plan mutation rules per failure type
- Attach richer correction metadata for trace logging
"""

from __future__ import annotations

from .types import CorrectionDecision, ExecutionPlan, FailureRecord


class SelfCorrectionLoop:
    """Decide whether a failed execution should be retried."""

    def handle_failure(
        self,
        plan: ExecutionPlan,
        failure: FailureRecord,
    ) -> CorrectionDecision:
        """
        Return a retry decision for a failed attempt.

        The scaffold keeps behavior minimal: retry while within the plan retry
        budget and return the same plan unchanged.
        """
        if failure.attempt >= plan.max_retries:
            return CorrectionDecision(
                retryable=False,
                reason="Retry budget exhausted",
                updated_plan=None,
            )

        return CorrectionDecision(
            retryable=True,
            reason="TODO: apply targeted correction strategy",
            updated_plan=plan,
        )
