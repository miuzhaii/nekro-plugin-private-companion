import asyncio
from pathlib import Path

from visuals import (
    PERSONA_REFERENCE_DESCRIPTION,
    PersonaVisualProfile,
    build_selfie_prompt,
    get_or_generate_schedule_selfie,
)


def test_build_selfie_prompt_includes_consistency_hints():
    profile = PersonaVisualProfile(
        character_prompt="银发蓝眼少女，蓝白连衣裙",
        style_prompt="统一的二次元厚涂风格",
        image_sha256="abc123",
        seed_hint="same character, same outfit",
    )
    prompt = build_selfie_prompt(
        {"window": "09:00-10:00", "activity": "写代码", "mood": "专注"},
        {"energy": 77, "conditions": [{"name": "困", "strength": 2}]},
        profile,
        hhmm="09:20",
    )

    assert "银发蓝眼少女" in prompt
    assert "统一的二次元厚涂风格" in prompt
    assert "必须保持同一角色外貌、服装配色和画风一致" in prompt
    assert "角色一致性锚点/参考图sha256：abc123" in prompt
    assert "一致性提示/seed：same character, same outfit" in prompt


def test_schedule_selfie_requires_generator_to_accept_reference_image(tmp_path: Path):
    ref = tmp_path / "persona_assets" / "persona.png"
    ref.parent.mkdir(parents=True)
    ref.write_bytes(b"fake-png")

    async def text_only_generator(prompt: str):
        return b"generated-image"

    profile = PersonaVisualProfile(
        character_prompt="固定角色",
        style_prompt="固定画风",
        reference_image="persona_assets/persona.png",
        image_sha256="refsha",
    )

    try:
        asyncio.run(
            get_or_generate_schedule_selfie(
                tmp_path,
                {"window": "10:00-11:00", "activity": "看书", "mood": "安静"},
                {"energy": 60},
                profile,
                date_key="2026-06-14",
                generator=text_only_generator,
                hhmm="10:05",
                retries=0,
            )
        )
    except TypeError as e:
        assert "reference_image" in str(e)
    else:
        raise AssertionError("expected reference image to be required, not silently ignored")
