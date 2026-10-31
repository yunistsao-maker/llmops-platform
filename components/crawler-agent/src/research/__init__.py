"""LLMOps portal research agent.

A Strands agent (run by the crawler-agent component as a plain CLI job; it ran on Amazon Bedrock AgentCore
before) that reads public sources,
checks every claim against quoted evidence, and returns *proposals* for the
LLMOps portal data files.  It never writes to the portal itself.
"""

__version__ = "0.1.0"
