"""Thin local stdio MCP adapter for the Omoide curation HTTP API.

The adapter owns protocol serialization only. It never imports the Omoide
application, never opens its database and never executes curation work locally:
every effect goes through the same authenticated `/api/curation` routes the
browser UI uses, under the same grant, policy and records.
"""

__version__ = '0.1.0'
