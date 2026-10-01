"""alibi — an alibi for your coding agent.

Reads the session transcripts an AI coding agent leaves behind, extracts every
sentence where the agent said it finished something, and checks each of those
sentences against the git working tree.

It never calls a model. It never judges intent. A claim it cannot settle
mechanically is reported as UNVERIFIED, and UNVERIFIED is not a pass.
"""

__version__ = "0.2.1"

__all__ = ["__version__"]
