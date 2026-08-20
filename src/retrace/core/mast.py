"""The fixed Multi-Agent System Failure Taxonomy (MAST) vocabulary."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FailureMode:
    """One failure mode in the MAST vocabulary."""

    id: str
    name: str
    category: str
    description: str


SPECIFICATION_AND_SYSTEM_DESIGN = "Specification & system design"
INTER_AGENT_MISALIGNMENT = "Inter-agent misalignment"
TASK_VERIFICATION_AND_TERMINATION = "Task verification & termination"

FAILURE_MODES: tuple[FailureMode, ...] = (
    FailureMode(
        "1.1",
        "Disobey task specification",
        SPECIFICATION_AND_SYSTEM_DESIGN,
        "The system does not follow the task's stated requirements or constraints, producing an incorrect or suboptimal outcome.",
    ),
    FailureMode(
        "1.2",
        "Disobey role specification",
        SPECIFICATION_AND_SYSTEM_DESIGN,
        "An agent does not respect the responsibilities or constraints of its assigned role and may instead behave as though it has another agent's role.",
    ),
    FailureMode(
        "1.3",
        "Step repetition",
        SPECIFICATION_AND_SYSTEM_DESIGN,
        "The system unnecessarily repeats steps that were already completed, which can delay completion or introduce errors.",
    ),
    FailureMode(
        "1.4",
        "Loss of conversation history",
        SPECIFICATION_AND_SYSTEM_DESIGN,
        "Conversation context is unexpectedly truncated: recent interactions are disregarded and the system reverts to an earlier conversational state.",
    ),
    FailureMode(
        "1.5",
        "Unaware of termination conditions",
        SPECIFICATION_AND_SYSTEM_DESIGN,
        "The system does not recognize or understand the criteria for ending the agents' interaction, causing it to continue unnecessarily.",
    ),
    FailureMode(
        "2.1",
        "Conversation reset",
        INTER_AGENT_MISALIGNMENT,
        "The dialogue restarts unexpectedly or without justification, potentially discarding context and progress from the interaction.",
    ),
    FailureMode(
        "2.2",
        "Fail to ask for clarification",
        INTER_AGENT_MISALIGNMENT,
        "An agent does not request additional information when data or instructions are unclear or incomplete, and may therefore take an incorrect action.",
    ),
    FailureMode(
        "2.3",
        "Task derailment",
        INTER_AGENT_MISALIGNMENT,
        "The agents deviate from the task's intended objective or focus, leading to irrelevant or unproductive actions.",
    ),
    FailureMode(
        "2.4",
        "Information withholding",
        INTER_AGENT_MISALIGNMENT,
        "An agent fails to communicate important information or insight it possesses that could affect another agent's decisions.",
    ),
    FailureMode(
        "2.5",
        "Ignore other agent's input",
        INTER_AGENT_MISALIGNMENT,
        "An agent disregards or inadequately considers another agent's input or recommendations, impairing decisions or collaboration.",
    ),
    FailureMode(
        "2.6",
        "Reasoning-action mismatch",
        INTER_AGENT_MISALIGNMENT,
        "The action an agent takes is inconsistent with its stated reasoning, resulting in unexpected or undesired behavior.",
    ),
    FailureMode(
        "3.1",
        "Premature termination",
        TASK_VERIFICATION_AND_TERMINATION,
        "A dialogue, interaction, or task ends before the required information has been exchanged or the objectives have been met, leaving an incomplete or incorrect result.",
    ),
    FailureMode(
        "3.2",
        "No or incomplete verification",
        TASK_VERIFICATION_AND_TERMINATION,
        "Checking or confirmation of task outcomes or system outputs is omitted or only partial, allowing errors or inconsistencies to remain undetected.",
    ),
    FailureMode(
        "3.3",
        "Incorrect verification",
        TASK_VERIFICATION_AND_TERMINATION,
        "Crucial information or decisions are not adequately validated or cross-checked during the interaction, leading to erroneous assurance of correctness.",
    ),
)

FAILURE_MODES_BY_ID = {mode.id: mode for mode in FAILURE_MODES}

FAILURE_MODE_CATEGORIES: tuple[tuple[str, tuple[FailureMode, ...]], ...] = tuple(
    (category, tuple(mode for mode in FAILURE_MODES if mode.category == category))
    for category in (
        SPECIFICATION_AND_SYSTEM_DESIGN,
        INTER_AGENT_MISALIGNMENT,
        TASK_VERIFICATION_AND_TERMINATION,
    )
)


def get_failure_mode(mode_id: str) -> FailureMode:
    """Return a failure mode by its MAST id, raising ``KeyError`` if unknown."""

    return FAILURE_MODES_BY_ID[mode_id]


__all__ = [
    "FAILURE_MODES",
    "FAILURE_MODES_BY_ID",
    "FAILURE_MODE_CATEGORIES",
    "FailureMode",
    "get_failure_mode",
]
