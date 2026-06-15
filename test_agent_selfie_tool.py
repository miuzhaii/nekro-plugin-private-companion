import importlib.util
import sys
from pathlib import Path

mod_path = Path(__file__).with_name("visuals.py")
spec = importlib.util.spec_from_file_location("pc_visuals_for_agent_tool_test", mod_path)
visuals = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = visuals
spec.loader.exec_module(visuals)


def test_selfie_capability_prompt_tells_agent_when_to_send_image():
    prompt = visuals.build_selfie_capability_prompt()

    assert "send_current_schedule_selfie" in prompt
    assert "日程自拍" in prompt
    assert "当前在做什么" in prompt
    assert "不要编造已经发送" in prompt


if __name__ == "__main__":
    test_selfie_capability_prompt_tells_agent_when_to_send_image()
    print("agent selfie tool prompt test passed")
