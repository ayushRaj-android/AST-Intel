"""Test fixture for various Python import styles."""

import os
import sys
import json
from pathlib import Path
from collections import defaultdict, OrderedDict
from typing import (
    Any,
    Dict,
    List,
    Optional,
)
from os.path import join, exists
from . import sibling_module
from ..parent import utils
import importlib as il


def use_imports() -> None:
    """Use some imports so they are meaningful."""
    _ = Path(".")
    _ = defaultdict(list)
