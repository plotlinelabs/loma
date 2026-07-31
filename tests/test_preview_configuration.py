from pathlib import Path


def test_preview_enables_integration_hub():
    script = Path("scripts/preview_up.sh").read_text()

    assert "INTEGRATION_HUB_ENABLED=true" in script
