"""Unit tests for src/sources/ubuntu_source.py — mock HTTP, no network."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.sources.base import SourceNotFound
from src.sources.ubuntu_source import UbuntuSource

CHANGELOG_TEXT = """\
vim (2:9.1.0016-1ubuntu1) noble; urgency=medium

  * Security fix for CVE-2024-22667.

 -- James McCoy <jamessan@debian.org>  Mon, 15 Jan 2024 07:11:08 -0500

vim (2:9.0.2189-2ubuntu1) mantic; urgency=medium

  * Merge from Debian unstable.

 -- Bryce Harrington <bryce@ubuntu.com>  Thu, 30 Nov 2023 12:22:46 -0800
"""


@pytest.fixture
def source() -> UbuntuSource:
    s = UbuntuSource()
    s.fetch.cache_clear()
    return s


async def test_fetch_parses_entries(source: UbuntuSource) -> None:
    with patch.object(source, "_fetch_text", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = CHANGELOG_TEXT
        result = await source.fetch("vim")

    assert len(result.entries) == 2
    assert result.source_name == "ubuntu"
    assert result.entries[0].version == "2:9.1.0016-1ubuntu1"
    assert "CVE-2024-22667" in result.entries[0].content


async def test_fetch_404_raises_not_found(source: UbuntuSource) -> None:
    with patch.object(source, "_fetch_text", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = SourceNotFound("vim")
        with pytest.raises(SourceNotFound):
            await source.fetch("vim")


async def test_fetch_url_pattern(source: UbuntuSource) -> None:
    with patch.object(source, "_fetch_text", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = CHANGELOG_TEXT
        await source.fetch("openssl")

    mock_fetch.assert_awaited_once_with(
        "https://changelogs.ubuntu.com/changelogs/binary/openssl/changelog"
    )
