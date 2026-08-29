import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent


def _install_stubs():
    # nekro_agent.core.config.config
    nekro = types.ModuleType("nekro_agent")
    core = types.ModuleType("nekro_agent.core")
    cfgmod = types.ModuleType("nekro_agent.core.config")
    cfgmod.config = SimpleNamespace(MODEL_GROUPS={})
    sys.modules.setdefault("nekro_agent", nekro)
    sys.modules.setdefault("nekro_agent.core", core)
    sys.modules.setdefault("nekro_agent.core.config", cfgmod)
    # plugin.get_config used by selfie_draw
    plugin = types.ModuleType("plugin")
    plugin.get_config = lambda: SimpleNamespace(
        SELFIE_MODEL_GROUP="",
        SELFIE_RETRIES=1,
        SELFIE_RETRY_DELAY_SECONDS=0,
    )
    sys.modules.setdefault("plugin", plugin)
    image_gen = types.ModuleType("image_gen")

    async def _fake(*a, **k):
        raise AssertionError("should not call generate in unit test")

    image_gen.generate_image_via_chat = _fake
    sys.modules.setdefault("image_gen", image_gen)


_STUB_KEYS = (
    "nekro_agent",
    "nekro_agent.core",
    "nekro_agent.core.config",
    "plugin",
    "image_gen",
)
_saved_modules = {k: sys.modules[k] for k in _STUB_KEYS if k in sys.modules}
_install_stubs()

_src = (ROOT / "selfie_draw.py").read_text(encoding="utf-8")
_src = _src.replace("from .image_gen import", "from image_gen import")
_src = _src.replace("from .plugin import", "from plugin import")
selfie_draw = types.ModuleType("selfie_draw")
sys.modules["selfie_draw"] = selfie_draw
exec(compile(_src, str(ROOT / "selfie_draw.py"), "exec"), selfie_draw.__dict__)

for k in _STUB_KEYS:
    if k in _saved_modules:
        sys.modules[k] = _saved_modules[k]
    else:
        sys.modules.pop(k, None)


class TestValidateSelfieConfig(unittest.TestCase):
    def test_enabled_empty_group_errors(self):
        err = selfie_draw.validate_selfie_config(True, "")
        self.assertIsInstance(err, str)
        self.assertTrue(err)
        self.assertTrue("模型组" in err or "SELFIE_MODEL_GROUP" in err)
        self.assertNotIn("http", err)
        self.assertNotRegex(err, r"\d+\.\d+\.\d+\.\d+")

    def test_enabled_whitespace_group_errors(self):
        err = selfie_draw.validate_selfie_config(True, "   ")
        self.assertTrue(err)

    def test_disabled_empty_ok(self):
        self.assertIsNone(selfie_draw.validate_selfie_config(False, ""))

    def test_enabled_named_group_ok(self):
        self.assertIsNone(selfie_draw.validate_selfie_config(True, "draw-main"))


class TestRateLimitHelper(unittest.TestCase):
    def test_status_code_429(self):
        exc = SimpleNamespace(status_code=429)
        self.assertTrue(selfie_draw.is_rate_limit_error(exc))

    def test_message_contains_429(self):
        self.assertTrue(selfie_draw.is_rate_limit_error(Exception("HTTP 429 Too Many Requests")))

    def test_other_error_not_rate_limit(self):
        self.assertFalse(selfie_draw.is_rate_limit_error(ValueError("no model group")))
