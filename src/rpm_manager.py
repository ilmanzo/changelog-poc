"""Local RPM database access — query metadata + parse changelogs."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

from async_lru import alru_cache

from .models import ChangelogEntry, PackageMetadata


class RPMManager:
    """Wraps ``rpm -q`` subprocess calls."""

    def __init__(self, rpm_binary: str = "rpm") -> None:
        self.rpm_binary = rpm_binary

    async def _exec(self, *args: str) -> tuple[str, str, int]:
        proc = await asyncio.create_subprocess_exec(
            self.rpm_binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return (
            stdout.decode("utf-8", errors="ignore").strip(),
            stderr.decode("utf-8", errors="ignore").strip(),
            proc.returncode or 0,
        )

    @alru_cache(maxsize=128)
    async def get_metadata(self, package_name: str) -> PackageMetadata:
        fmt = "%{NAME}|%{VERSION}|%{RELEASE}|%{URL}"
        stdout, stderr, rc = await self._exec("-q", "--qf", fmt, "--", package_name)

        if rc != 0:
            raise RuntimeError(f"Package '{package_name}' not found: {stderr}")

        parts = stdout.split("|")
        name, version, release, raw_url = parts[0], parts[1], parts[2], parts[3]
        url: str | None = raw_url if raw_url and raw_url != "(none)" else None

        raw_changelog, _, _ = await self._exec("-q", "--changelog", "--", package_name)
        parsed_entries = self.parse_changelog(raw_changelog)

        return PackageMetadata(
            name=name,
            version=version,
            release=release,
            url=url,
            changelog=parsed_entries,
        )

    @alru_cache(maxsize=128)
    async def get_dependencies(self, package_name: str) -> frozenset[str]:
        """Direct runtime deps resolved to providing package names. Self excluded."""
        stdout, stderr, rc = await self._exec("-qR", "--", package_name)
        if rc != 0:
            raise RuntimeError(f"Package '{package_name}' not found: {stderr}")

        tokens: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line or line.startswith(("rpmlib(", "config(")):
                continue
            tokens.append(line.split()[0])

        if not tokens:
            return frozenset()

        out, _, _ = await self._exec("-q", "--whatprovides", "--qf", "%{NAME}\n", "--", *tokens)
        deps: set[str] = set()
        for line in out.splitlines():
            name = line.strip()
            if name and not name.startswith("no package provides") and not name.startswith("error:"):
                deps.add(name)
        deps.discard(package_name)
        return frozenset(deps)

    @alru_cache(maxsize=128)
    async def get_reverse_dependencies(self, package_name: str) -> frozenset[str]:
        out, err, rc = await self._exec("-q", "--provides", "--", package_name)
        if rc != 0:
            raise RuntimeError(f"Package '{package_name}' not found: {err}")

        capabilities: set[str] = {package_name}
        for line in out.splitlines():
            tok = line.strip()
            if tok:
                capabilities.add(tok.split()[0])

        out, _, _ = await self._exec(
            "-q", "--whatrequires", "--qf", "%{NAME}\n", "--", *capabilities
        )
        rdeps: set[str] = set()
        for line in out.splitlines():
            name = line.strip()
            if name and not name.startswith(("no package", "error:")):
                rdeps.add(name)
        rdeps.discard(package_name)
        return frozenset(rdeps)

    _HEADER_RE = re.compile(r"^\* ([A-Z][a-z]{2} [A-Z][a-z]{2} \d{2} \d{4}) (.*)$")
    _BACKFILL_VERSION_RE = re.compile(
        r"^[ \t]*- (?:Update|Upgrade) to (?:version )?([\d\.]+)",
        re.MULTILINE | re.IGNORECASE,
    )

    @staticmethod
    def parse_changelog(raw_text: str) -> list[ChangelogEntry]:
        """Parse RPM ``--changelog`` output into ChangelogEntry list."""
        entries = RPMManager._parse_header_blocks(raw_text)
        RPMManager._backfill_missing_versions(entries)
        return entries

    @staticmethod
    def _parse_header_blocks(raw_text: str) -> list[ChangelogEntry]:
        entries: list[ChangelogEntry] = []
        current_header: tuple[str, str, str] | None = None
        current_content: list[str] = []

        for line in raw_text.splitlines():
            match = RPMManager._HEADER_RE.match(line)
            if match:
                if current_header:
                    entries.append(RPMManager._create_entry(current_header, current_content))

                header_text = match.group(2)
                version = "unknown"
                if " - " in header_text:
                    # Why: rpm header convention is "Author Name <email> - version-release";
                    # rsplit on the last " - " keeps email addresses (which may contain dashes)
                    # attached to the author rather than misclassified as the version.
                    author, version = header_text.rsplit(" - ", 1)
                else:
                    author = header_text

                current_header = (match.group(1), author, version)
                current_content = []
            elif current_header:
                current_content.append(line)

        if current_header:
            entries.append(RPMManager._create_entry(current_header, current_content))

        return entries

    @staticmethod
    def _backfill_missing_versions(entries: list[ChangelogEntry]) -> None:
        """Mutates *entries* in place: when the header lacked a version, look in the body."""
        for idx, e in enumerate(entries):
            if e.version != "unknown":
                continue
            v_match = RPMManager._BACKFILL_VERSION_RE.search(e.content)
            if v_match:
                entries[idx] = ChangelogEntry(
                    version=v_match.group(1),
                    author=e.author,
                    date=e.date,
                    content=e.content,
                )

    @staticmethod
    def _create_entry(
        header: tuple[str, str, str], content: list[str]
    ) -> ChangelogEntry:
        date_str, author, version = header
        try:
            dt = datetime.strptime(date_str, "%a %b %d %Y")
        except ValueError:
            dt = datetime.min

        return ChangelogEntry(
            version=version.strip(),
            author=author.strip(),
            date=dt,
            content="\n".join(content).strip(),
        )
