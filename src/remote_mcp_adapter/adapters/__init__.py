"""Tool adapter hooks for upload-consuming and artifact-producing tools."""

from .artifact_producer import handle_artifact_producer_tool
from .upload_consumer import handle_upload_consumer_tool

__all__ = ["handle_upload_consumer_tool", "handle_artifact_producer_tool"]
