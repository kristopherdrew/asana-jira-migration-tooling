from __future__ import annotations

import importlib.util
from pathlib import Path
import types
import unittest


def _load_module_from_path(module_name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module {module_name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str) -> unittest.TestSuite:
    root = Path(__file__).parent
    suite = unittest.TestSuite()
    for path in root.rglob("*.test.py"):
        relative = path.relative_to(root)
        module_name = "tests." + ".".join(part.replace("-", "_") for part in relative.parts)
        module_name = module_name.replace(".test", "_test").rstrip(".py")
        module = _load_module_from_path(module_name, path)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite
