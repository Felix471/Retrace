from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_ROOT = REPO_ROOT / "fixtures"
FIXTURE_ROOT = FIXTURES_ROOT / "avalon_mini"
FIXTURE_PATH = FIXTURE_ROOT / "games.jsonl"
SCRUB_SCRIPT = FIXTURE_ROOT / "scrub.py"
RAW_SAMPLE = REPO_ROOT / "reference-logs" / "avalon_sample.jsonl"

EXPECTED_GAME_IDS = [
    "1776453329940-bvvf9",
    "1776470168031-kwy8o",
    "1776471689022-f5veq",
    "1776642987532-fob9d",
    "1776701240615-6ius8",
]
SCRUB_MARKER = "…[scrubbed]"
_TAG_NAME = "thinking"
_OPEN_TAG = f"<{_TAG_NAME}>"
_CLOSE_TAG = f"</{_TAG_NAME}>"
# Keep the sensitive probe non-contiguous in committed test source.
_FORBIDDEN_PHRASE = " ".join(  # noqa: FLY002
    ("The", "user", "wants", "me", "to", "act", "as", "Player")
)


def _load_fixture() -> tuple[bytes, list[str], list[dict]]:
    raw = FIXTURE_PATH.read_bytes()
    lines = raw.decode("utf-8").splitlines()
    games = [json.loads(line) for line in lines]
    return raw, lines, games


def _discussion_contents(games: list[dict]) -> list[tuple[int, int, str]]:
    contents: list[tuple[int, int, str]] = []
    for line_number, game in enumerate(games, start=1):
        for discussion_index, discussion in enumerate(game["discussions"]):
            content = discussion["content"]
            if not isinstance(content, str):
                pytest.fail(
                    f"non-string content at line {line_number}, discussion {discussion_index}",
                    pytrace=False,
                )
            contents.append((line_number, discussion_index, content))
    return contents


def _files_are_byte_equal(first: Path, second: Path) -> bool:
    return first.read_bytes() == second.read_bytes()


def test_fixture_is_canonical_jsonl() -> None:
    raw, lines, games = _load_fixture()

    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")
    assert len(lines) == 5
    assert len(games) == 5
    assert all(lines)
    for line_number, (game, line) in enumerate(zip(games, lines, strict=True), start=1):
        canonical = json.dumps(game, ensure_ascii=False, separators=(",", ":"))
        if canonical != line:
            pytest.fail(f"fixture line {line_number} is not compact JSON", pytrace=False)


def test_fixture_has_expected_game_ids() -> None:
    _, _, games = _load_fixture()

    assert [game["gameId"] for game in games] == EXPECTED_GAME_IDS


def test_known_quest_defect_is_preserved() -> None:
    _, _, games = _load_fixture()
    quest = games[1]["quests"][4]

    assert quest["round"] == 4
    assert quest["result"] == "fail"
    assert isinstance(quest["actions"], dict)
    assert quest["actions"]
    assert set(quest["actions"].values()) == {"success"}


def test_synthetic_leak_shape() -> None:
    _, _, games = _load_fixture()
    contents = _discussion_contents(games)
    starts = [(line, index) for line, index, content in contents if content.startswith(_OPEN_TAG)]
    unclosed = [
        (line, index)
        for line, index, content in contents
        if _OPEN_TAG in content and _CLOSE_TAG not in content
    ]

    assert starts == [(3, 110)]
    assert unclosed == starts
    content = next(content for line, index, content in contents if (line, index) == starts[0])
    valid_shape = (
        content.startswith(f"{_OPEN_TAG}\n")
        and len(content) == 500
        and _CLOSE_TAG not in content
    )
    if not valid_shape:
        pytest.fail("synthetic leak shape is invalid at line 3, discussion 110", pytrace=False)
    trailing_fragment = content.rsplit(" ", maxsplit=1)[-1]
    midword_cut = (
        bool(trailing_fragment)
        and trailing_fragment != "echoes"
        and "echoes".startswith(trailing_fragment)
    )
    if not midword_cut:
        pytest.fail("synthetic leak does not end mid-word", pytrace=False)
    roles = [
        player["role"].casefold()
        for player in games[2]["players"]
        if isinstance(player.get("role"), str) and player["role"]
    ]
    if any(role in content.casefold() for role in roles):
        pytest.fail("synthetic leak contains a source role name", pytrace=False)


def test_discussion_content_bounds_and_newline() -> None:
    _, _, games = _load_fixture()
    contents = _discussion_contents(games)
    ordinary = [content for _, _, content in contents if not content.startswith(_OPEN_TAG)]
    marked = [content for content in ordinary if content.endswith(SCRUB_MARKER)]

    if any(len(content) > 500 for _, _, content in contents):
        pytest.fail("a discussion exceeds 500 characters", pytrace=False)
    if any(len(content) > 91 for content in ordinary):
        pytest.fail("an ordinary discussion exceeds 91 characters", pytrace=False)
    assert marked
    if any(len(content) != 91 for content in marked):
        pytest.fail("a marked discussion does not have the required length", pytrace=False)
    assert any("\n" in content for _, _, content in contents)


def test_sensitive_text_is_absent_from_fixture_tree() -> None:
    forbidden = _FORBIDDEN_PHRASE.encode()

    for path in FIXTURES_ROOT.rglob("*"):
        if not path.is_file():
            continue
        contents = path.read_bytes()
        if forbidden in contents:
            relative_path = path.relative_to(REPO_ROOT)
            pytest.fail(f"forbidden text found in {relative_path}", pytrace=False)

    tag_files = [
        path
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file() and _OPEN_TAG.encode() in path.read_bytes()
    ]
    assert tag_files == [FIXTURE_PATH]


@pytest.mark.skipif(not RAW_SAMPLE.is_file(), reason="local raw sample is unavailable")
def test_scrub_regeneration_is_byte_identical(tmp_path: Path) -> None:
    first_output = tmp_path / "first.jsonl"
    second_output = tmp_path / "second.jsonl"

    for output_path in (first_output, second_output):
        subprocess.run(
            [sys.executable, str(SCRUB_SCRIPT), str(RAW_SAMPLE), str(output_path)],
            check=True,
            cwd=REPO_ROOT,
        )

    assert _files_are_byte_equal(first_output, second_output)
    assert _files_are_byte_equal(first_output, FIXTURE_PATH)
