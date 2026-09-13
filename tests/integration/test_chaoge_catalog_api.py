"""Original styles reach the catalog API while old choices remain available."""

import hashlib

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_catalog_serves_original_descriptions_and_preserves_legacy(
    alice: AsyncClient,
) -> None:
    response = await alice.post("/api/v1/projects", json={"title": "原版画风核验"})
    assert response.status_code == 201, response.text
    pid = response.json()["id"]
    response = await alice.get(f"/api/v1/projects/{pid}/lock-variables")
    assert response.status_code == 200, response.text
    options = response.json()["style_options"]
    rows = [row for row in options if row["key"].startswith("chaoge_v1_")]
    assert [row["key"] for row in rows] == [f"chaoge_v1_{i:02}" for i in range(1, 9)]
    text = "\n".join(
        row[key] for row in rows for key in ("character_tokens", "scene_tokens", "video_tokens")
    )
    assert hashlib.sha256(text.encode()).hexdigest() == (
        "6812384de2647cb40fcb29732e9025e3c7b0d64622240989c273d44fff74d980"
    )
    legacy = next(row for row in options if row["key"] == "anime_suspense")
    assert legacy["character_tokens"] == "日式动画风格，赛璐璐上色，清晰线稿"
