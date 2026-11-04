"""Role C: confirmed-alert publishing; configured by A's existing plugin hook."""
from .publisher import publish_alert

__all__ = ["publish_alert"]
