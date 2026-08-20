from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from retrace.core.mast import FAILURE_MODE_CATEGORIES
from retrace.server.app import create_app


def test_vocabulary_endpoint_contract(tmp_path: Path) -> None:
    async def check() -> None:
        app = create_app(tmp_path / "unused.db")
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        ):
            response = await client.get("/api/tags/vocabulary")
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/json")
            expected = {
                "categories": [
                    {
                        "category": category,
                        "modes": [
                            {
                                "id": mode.id,
                                "name": mode.name,
                                "category": mode.category,
                                "description": mode.description,
                            }
                            for mode in modes
                        ],
                    }
                    for category, modes in FAILURE_MODE_CATEGORIES
                ]
            }
            assert response.json() == expected
            assert sum(len(group["modes"]) for group in response.json()["categories"]) == 14

            schema = (await client.get("/openapi.json")).json()
            response_schema = schema["paths"]["/api/tags/vocabulary"]["get"][
                "responses"
            ]["200"]["content"]["application/json"]["schema"]
            assert response_schema["$ref"].endswith("/TagVocabularyResponse")
            assert "TagVocabularyResponse" in schema["components"]["schemas"]
            assert "FailureModeResponse" in schema["components"]["schemas"]

    asyncio.run(check())
