"""Generate the deterministic scrubbed Avalon fixture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_PATH = REPO_ROOT / "reference-logs" / "avalon_sample.jsonl"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "fixtures" / "avalon_mini" / "games.jsonl"

ORDINARY_CONTENT_LIMIT = 80
MAX_CONTENT_LENGTH = 500
SCRUB_MARKER = "…[scrubbed]"
EXPECTED_SYNTHETIC_LEAKS = 1

_TAG_NAME = "thinking"
_OPEN_TAG = f"<{_TAG_NAME}>"
_CLOSE_TAG = f"</{_TAG_NAME}>"
_SYNTHETIC_TEMPLATE = f"{_OPEN_TAG}\nSynthetic filler only. "
_SYNTHETIC_FILLER = (
    "Lantern clouds drift over paper valleys while gentle echoes trace invented paths. "
)
# Keep the sensitive probe non-contiguous in committed source.
_FORBIDDEN_PHRASE = " ".join(  # noqa: FLY002
    ("The", "user", "wants", "me", "to", "act", "as", "Player")
)

_ERR_BAD_TEMPLATE = "The synthetic leak template does not preserve the required shape."
_ERR_INPUT_EQUALS_OUTPUT = "Input and output paths must be different."
_ERR_EMPTY_INPUT = "The input must contain at least one JSON object."
_ERR_INVALID_JSON = "The JSONL input or output contains an invalid JSON object."
_ERR_INVALID_DISCUSSIONS = "A record has an invalid discussions array."
_ERR_INVALID_CONTENT = "A discussion has a non-string content field."
_ERR_PRESERVATION = "Scrubbing changed data outside discussion content."
_ERR_UNSAFE_CONTENT = "The scrubbed output failed its content safety checks."
_ERR_ROLE_IN_FILLER = "The synthetic filler contains a source role name."
_ERR_ENCODING = "The output is not canonical UTF-8 JSONL with LF line endings."


class ScrubValidationError(RuntimeError):
    """Raised when scrubbed output violates a safety invariant."""


def _build_synthetic_leak() -> str:
    source = _SYNTHETIC_TEMPLATE + (_SYNTHETIC_FILLER * MAX_CONTENT_LENGTH)
    scrubbed = source[:MAX_CONTENT_LENGTH]
    shape_is_valid = (
        len(scrubbed) == MAX_CONTENT_LENGTH
        and scrubbed.startswith(f"{_OPEN_TAG}\n")
        and _CLOSE_TAG not in scrubbed
        and scrubbed[-1].isalpha()
        and source[MAX_CONTENT_LENGTH].isalpha()
    )
    if not shape_is_valid:
        raise ScrubValidationError(_ERR_BAD_TEMPLATE)
    return scrubbed


SYNTHETIC_LEAK = _build_synthetic_leak()


def _parse_jsonl(text: str) -> list[dict]:
    lines = text.splitlines()
    if not lines:
        raise ScrubValidationError(_ERR_EMPTY_INPUT)
    try:
        games = [json.loads(line) for line in lines]
    except json.JSONDecodeError as error:
        raise ScrubValidationError(_ERR_INVALID_JSON) from error
    if not all(isinstance(game, dict) for game in games):
        raise ScrubValidationError(_ERR_INVALID_JSON)
    return games


def _load_games(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ScrubValidationError(_ERR_INVALID_JSON) from error
    return _parse_jsonl(text)


def _clone_games(games: list[dict]) -> list[dict]:
    cloned = json.loads(json.dumps(games, ensure_ascii=False))
    if not isinstance(cloned, list) or not all(isinstance(game, dict) for game in cloned):
        raise ScrubValidationError(_ERR_INVALID_JSON)
    return cloned


def _discussions(game: dict) -> list[dict]:
    discussions = game.get("discussions")
    if not isinstance(discussions, list) or not all(
        isinstance(discussion, dict) for discussion in discussions
    ):
        raise ScrubValidationError(_ERR_INVALID_DISCUSSIONS)
    return discussions


def _has_unclosed_tag(content: str) -> bool:
    return _OPEN_TAG in content and _CLOSE_TAG not in content


def _scrub_content(content: str) -> str:
    if _has_unclosed_tag(content):
        return SYNTHETIC_LEAK
    if len(content) > ORDINARY_CONTENT_LIMIT:
        return content[:ORDINARY_CONTENT_LIMIT] + SCRUB_MARKER
    return content


def _scrub_games(games: list[dict]) -> None:
    for game in games:
        for discussion in _discussions(game):
            content = discussion.get("content")
            if not isinstance(content, str):
                raise ScrubValidationError(_ERR_INVALID_CONTENT)
            discussion["content"] = _scrub_content(content)


def _validate_preservation(original: list[dict], scrubbed: list[dict]) -> None:
    if len(original) != len(scrubbed):
        raise ScrubValidationError(_ERR_PRESERVATION)

    for original_game, scrubbed_game in zip(original, scrubbed, strict=True):
        if list(original_game) != list(scrubbed_game):
            raise ScrubValidationError(_ERR_PRESERVATION)
        for key, value in original_game.items():
            if key != "discussions" and scrubbed_game[key] != value:
                raise ScrubValidationError(_ERR_PRESERVATION)

        original_discussions = _discussions(original_game)
        scrubbed_discussions = _discussions(scrubbed_game)
        if len(original_discussions) != len(scrubbed_discussions):
            raise ScrubValidationError(_ERR_PRESERVATION)

        for original_discussion, scrubbed_discussion in zip(
            original_discussions,
            scrubbed_discussions,
            strict=True,
        ):
            if list(original_discussion) != list(scrubbed_discussion):
                raise ScrubValidationError(_ERR_PRESERVATION)
            for key, value in original_discussion.items():
                expected = _scrub_content(value) if key == "content" else value
                if scrubbed_discussion[key] != expected:
                    raise ScrubValidationError(_ERR_PRESERVATION)


def _validate_role_names(games: list[dict]) -> None:
    synthetic = SYNTHETIC_LEAK.casefold()
    for game in games:
        players = game.get("players", [])
        if not isinstance(players, list):
            raise ScrubValidationError(_ERR_ROLE_IN_FILLER)
        for player in players:
            if not isinstance(player, dict):
                raise ScrubValidationError(_ERR_ROLE_IN_FILLER)
            role = player.get("role")
            if isinstance(role, str) and role and role.casefold() in synthetic:
                raise ScrubValidationError(_ERR_ROLE_IN_FILLER)


def _serialize_games(games: list[dict]) -> str:
    lines = [
        json.dumps(game, ensure_ascii=False, separators=(",", ":")) for game in games
    ]
    return "\n".join(lines) + "\n"


def _validate_content(games: list[dict], serialized: str) -> None:
    synthetic_count = 0
    if _FORBIDDEN_PHRASE in serialized:
        raise ScrubValidationError(_ERR_UNSAFE_CONTENT)

    for game in games:
        for discussion in _discussions(game):
            content = discussion.get("content")
            if not isinstance(content, str) or len(content) > MAX_CONTENT_LENGTH:
                raise ScrubValidationError(_ERR_UNSAFE_CONTENT)
            if _has_unclosed_tag(content):
                if content != SYNTHETIC_LEAK:
                    raise ScrubValidationError(_ERR_UNSAFE_CONTENT)
                synthetic_count += 1
            elif len(content) > ORDINARY_CONTENT_LIMIT + len(SCRUB_MARKER):
                raise ScrubValidationError(_ERR_UNSAFE_CONTENT)

    if synthetic_count != EXPECTED_SYNTHETIC_LEAKS:
        raise ScrubValidationError(_ERR_UNSAFE_CONTENT)
    _validate_role_names(games)


def _validate_written_output(path: Path) -> None:
    raw = path.read_bytes()
    canonical_bytes = (
        not raw.startswith(b"\xef\xbb\xbf")
        and b"\r" not in raw
        and raw.endswith(b"\n")
        and not raw.endswith(b"\n\n")
    )
    if not canonical_bytes:
        raise ScrubValidationError(_ERR_ENCODING)
    try:
        serialized = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ScrubValidationError(_ERR_ENCODING) from error
    games = _parse_jsonl(serialized)
    if _serialize_games(games) != serialized:
        raise ScrubValidationError(_ERR_ENCODING)
    _validate_content(games, serialized)


def scrub_file(input_path: Path, output_path: Path) -> None:
    """Scrub one JSONL sample into a safe deterministic fixture."""
    if input_path.resolve() == output_path.resolve():
        raise ScrubValidationError(_ERR_INPUT_EQUALS_OUTPUT)

    original_games = _load_games(input_path)
    scrubbed_games = _clone_games(original_games)
    _scrub_games(scrubbed_games)
    _validate_preservation(original_games, scrubbed_games)

    serialized = _serialize_games(scrubbed_games)
    _validate_content(scrubbed_games, serialized)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(serialized, encoding="utf-8", newline="\n")
    _validate_written_output(output_path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input_path",
        nargs="?",
        type=Path,
        default=DEFAULT_INPUT_PATH,
        help="source JSONL path",
    )
    parser.add_argument(
        "output_path",
        nargs="?",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="destination fixture path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the scrubber command-line interface."""
    args = _build_parser().parse_args(argv)
    scrub_file(args.input_path, args.output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
