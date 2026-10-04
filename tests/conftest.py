"""Tests never send traces anywhere, whatever the local .env says."""

import os

os.environ["LANGSMITH_TRACING"] = "false"
