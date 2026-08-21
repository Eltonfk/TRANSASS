"""Non-loadable fixture representation of the future structured tool.

It deliberately accepts no data that could alter execution identity.
"""

TOOL_ID = "subtranslate_recovery_apply_once"
ARGUMENTS = {}


def invoke(broker, arguments):
    if arguments != ARGUMENTS:
        raise ValueError("ZERO_ARGUMENT_CONTRACT_VIOLATION")
    return broker.execute_zero_args(arguments)
