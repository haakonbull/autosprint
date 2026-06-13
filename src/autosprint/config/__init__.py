"""Configuration layer: the pydantic-settings singleton and TOML rendering.

Re-exports the public surface so call sites keep using
``from autosprint.config import config`` rather than reaching into the
submodule.
"""

from autosprint.config.settings import (
    ENV_SET_FIELDS,
    SPEAK_LEVELS,
    Config,
    _project_root,
    config,
)
from autosprint.config.toml_io import render_config_toml

__all__ = [
    "ENV_SET_FIELDS",
    "SPEAK_LEVELS",
    "Config",
    "_project_root",
    "config",
    "render_config_toml",
]
