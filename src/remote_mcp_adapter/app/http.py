"""Compatibility facade for HTTP middleware and route registration."""

from __future__ import annotations

from .http_registration import register_middlewares, register_routes

__all__ = ["register_middlewares", "register_routes"]
