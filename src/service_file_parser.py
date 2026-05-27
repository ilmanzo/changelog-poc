"""Parse OBS _service XML files to extract upstream repository URLs.

OBS service files define how source archives are fetched. The ``obs_scm``
and ``tar_scm`` services carry a ``<param name="url">`` element pointing
at the upstream VCS repository.
"""
from __future__ import annotations

import defusedxml.ElementTree as ET

_SCM_SERVICE_NAMES = frozenset({
    "obs_scm",
    "tar_scm",
    "obs_scm_bridge",
    "download_url",
})


def extract_urls_from_service(xml_text: str) -> list[str]:
    """Return upstream URLs found in an OBS _service XML file."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    urls: list[str] = []
    for service in root.iter("service"):
        svc_name = service.get("name", "")
        if svc_name not in _SCM_SERVICE_NAMES:
            continue
        for param in service.iter("param"):
            if param.get("name") == "url" and param.text:
                url = param.text.strip()
                if url.startswith(("https://", "http://")):
                    urls.append(url)
    return urls
