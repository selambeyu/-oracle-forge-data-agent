"""
Execution engine scaffold for the Oracle Forge runtime.

This module owns orchestration only:
- choose MCP or sandbox route per step
- collect typed execution traces
- delegate retries to the self-correction component

Real query planning, merge semantics, and validation logic stay out of this
scaffold for now. Those behaviors should be added behind the typed interfaces
defined in ``agent.types``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .mcp_client import MCPClient
from .sandbox_client import SandboxClient
from .self_correction import SelfCorrectionLoop
from .types import (
    CorrectionDecision,
    ExecutionPlan,
    ExecutionResult,
    ExecutionStatus,
    ExecutionStep,
    ExecutionTrace,
    FailureRecord,
    MCPToolCall,
    SandboxExecutionRequest,
    StepKind,
    StepRoute,
)


class ExecutionEngine:
    """Coordinate execution across MCP, sandbox, and self-correction layers."""

    def __init__(
        self,
        mcp_client: Optional[MCPClient] = None,
        sandbox_client: Optional[SandboxClient] = None,
        self_correction: Optional[SelfCorrectionLoop] = None,
    ) -> None:
        self.mcp_client = mcp_client or MCPClient()
        self.sandbox_client = sandbox_client or SandboxClient()
        self.self_correction = self_correction or SelfCorrectionLoop()

    def execute_plan(
        self,
        plan: ExecutionPlan,
        context: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """
        Execute a typed plan with bounded retries.

        TODO: Replace the generic state passing with richer per-step input/output
        mapping once the planner contract is finalized.
        """
        runtime_context: Dict[str, Any] = dict(context or {})
        working_plan = plan
        trace: list[ExecutionTrace] = []
        outputs: Dict[str, Any] = {}
        correction_applied = False

        for attempt in range(1, working_plan.max_retries + 1):
            outputs = {}
            failed_trace: ExecutionTrace | None = None

            for step in working_plan.steps:
                step_trace = self._execute_step(
                    step=step,
                    context=runtime_context,
                    outputs=outputs,
                    attempt=attempt,
                )
                trace.append(step_trace)

                if step_trace.status is ExecutionStatus.SUCCEEDED:
                    if step_trace.output_key is not None:
                        outputs[step_trace.output_key] = step_trace.output
                    continue

                failed_trace = step_trace
                break

            if failed_trace is None:
                final_output = outputs.get(working_plan.final_output_key)
                if final_output is None and outputs:
                    final_output = outputs[next(reversed(outputs))]
                return ExecutionResult(
                    success=True,
                    status=ExecutionStatus.SUCCEEDED,
                    final_output=final_output,
                    outputs=outputs,
                    trace=trace,
                    attempts=attempt,
                    correction_applied=correction_applied,
                )

            failure = FailureRecord(
                step_id=failed_trace.step_id,
                route=failed_trace.route,
                error=failed_trace.error or "Step failed",
                attempt=attempt,
                trace=trace.copy(),
            )
            decision = self.self_correction.handle_failure(working_plan, failure)

            if not decision.retryable or decision.updated_plan is None:
                return ExecutionResult(
                    success=False,
                    status=ExecutionStatus.FAILED,
                    final_output=None,
                    outputs=outputs,
                    trace=trace,
                    attempts=attempt,
                    correction_applied=correction_applied,
                    error=failed_trace.error or decision.reason,
                )

            correction_applied = True
            working_plan = decision.updated_plan
            trace.append(
                ExecutionTrace(
                    step_id=failed_trace.step_id,
                    step_kind=StepKind.VALIDATE,
                    route=StepRoute.SELF_CORRECTION,
                    status=ExecutionStatus.RETRYING,
                    attempt=attempt,
                    execution_time=0.0,
                    output=None,
                    output_key=None,
                    error=None,
                    metadata={"reason": decision.reason},
                )
            )

        return ExecutionResult(
            success=False,
            status=ExecutionStatus.FAILED,
            final_output=None,
            outputs=outputs,
            trace=trace,
            attempts=working_plan.max_retries,
            correction_applied=correction_applied,
            error="Execution exhausted retry budget",
        )

    def _execute_step(
        self,
        step: ExecutionStep,
        context: Dict[str, Any],
        outputs: Dict[str, Any],
        attempt: int,
    ) -> ExecutionTrace:
        route = step.route or self._default_route(step)
        started_at = time.time()

        try:
            if route is StepRoute.MCP_TOOLBOX:
                result = self._execute_mcp_step(step, context, outputs)
            elif route is StepRoute.SANDBOX:
                result = self._execute_sandbox_step(step, context, outputs, attempt)
            else:
                raise ValueError(f"Unsupported execution route: {route.value}")
        except Exception as exc:  # pragma: no cover - defensive scaffold
            return ExecutionTrace(
                step_id=step.step_id,
                step_kind=step.kind,
                route=route,
                status=ExecutionStatus.FAILED,
                attempt=attempt,
                execution_time=round(time.time() - started_at, 4),
                output=None,
                output_key=step.output_key,
                error=str(exc),
                metadata={},
            )

        status = ExecutionStatus.SUCCEEDED if result.success else ExecutionStatus.FAILED
        return ExecutionTrace(
            step_id=step.step_id,
            step_kind=step.kind,
            route=route,
            status=status,
            attempt=attempt,
            execution_time=round(time.time() - started_at, 4),
            output=result.result if route is StepRoute.SANDBOX and result.success else (
                result.data if result.success else None
            ),
            output_key=step.output_key,
            error=getattr(result, "error_if_any", None) if route is StepRoute.SANDBOX else result.error,
            metadata=self._build_trace_metadata(step, route, result),
        )

    def _execute_mcp_step(
        self,
        step: ExecutionStep,
        context: Dict[str, Any],
        outputs: Dict[str, Any],
    ):
        tool_name = step.tool_name
        if not tool_name:
            raise ValueError(f"MCP step '{step.step_id}' is missing tool_name")

        parameters = dict(step.parameters)
        if step.code and "query" not in parameters:
            parameters["query"] = step.code

        request = MCPToolCall(
            tool_name=tool_name,
            parameters=parameters,
            database_type=step.database_type,
            context=self._build_step_context(step, context, outputs),
        )
        return self.mcp_client.call_tool(request)

    def _execute_sandbox_step(
        self,
        step: ExecutionStep,
        context: Dict[str, Any],
        outputs: Dict[str, Any],
        attempt: int,
    ):
        request = SandboxExecutionRequest(
            code_plan=step.code or "",
            trace_id=f"{step.step_id}:attempt-{attempt}",
            inputs_payload={ref: outputs.get(ref) for ref in step.input_refs} or None,
            db_type=step.database_type or "transform",
            context={"shared_context": context},
            step_id=step.step_id,
        )
        return self.sandbox_client.execute(request)

    @staticmethod
    def _build_step_context(
        step: ExecutionStep,
        context: Dict[str, Any],
        outputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "shared_context": context,
            "inputs": {ref: outputs.get(ref) for ref in step.input_refs},
        }

    @staticmethod
    def _default_route(step: ExecutionStep) -> StepRoute:
        if step.kind is StepKind.DATABASE:
            return StepRoute.MCP_TOOLBOX
        return StepRoute.SANDBOX

    @staticmethod
    def _build_trace_metadata(step: ExecutionStep, route: StepRoute, result: Any) -> Dict[str, Any]:
        if route is StepRoute.SANDBOX:
            return {
                "validation_status": getattr(result, "validation_status", None),
                "sandbox_trace": getattr(result, "trace", []),
            }

        return {"tool_name": getattr(result, "tool_name", None)}


__all__ = [
    "CorrectionDecision",
    "ExecutionEngine",
    "ExecutionPlan",
    "ExecutionResult",
    "ExecutionStatus",
    "ExecutionStep",
    "ExecutionTrace",
    "FailureRecord",
    "StepKind",
    "StepRoute",
]
