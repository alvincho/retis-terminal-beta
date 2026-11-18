"""Readers for configured vendor API documentation sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import glob
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
from typing import Iterable
from urllib.parse import urljoin, urlparse

import httpx

from .models import VendorSpecSource


_TEXT_SUFFIXES = {".html", ".htm", ".json", ".md", ".rst", ".txt", ".xml"}
_TEXT_MEDIA_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/yaml",
    "application/x-yaml",
    "application/vnd.oai.openapi",
)


def _normalized_host(value: str) -> str:
    return str(value or "").strip().lower().rstrip(".")


def _host_is_public(host: str) -> bool:
    """Return whether every resolved address is safe for configured web ingestion."""
    normalized = _normalized_host(host)
    if not normalized or normalized in {"localhost", "localhost.localdomain"}:
        return False
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(normalized, None)}
    except socket.gaierror:
        return False
    if not addresses:
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if any(
            (
                ip.is_private,
                ip.is_loopback,
                ip.is_link_local,
                ip.is_multicast,
                ip.is_reserved,
                ip.is_unspecified,
            )
        ):
            return False
    return True


class AbstractSpecReader(ABC):
    """Read one configured vendor specification source."""

    @abstractmethod
    def supports(self, source: VendorSpecSource) -> bool:
        """Return whether this reader supports the configured source."""

    @abstractmethod
    def read(self, source: VendorSpecSource) -> str:
        """Read the source and return its textual representation."""


@dataclass(frozen=True)
class WebsiteSpecReader(AbstractSpecReader):
    """Read approved HTTP or HTTPS vendor documentation."""

    timeout_sec: float = 30.0
    max_bytes: int = 8 * 1024 * 1024
    user_agent: str = "AttasDataAgentSpecReader/0.1"
    _MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\((https?://[^)]+)\)")
    _HTML_LINK_RE = re.compile(r"<a\b[^>]*\bhref=[\"']([^\"']+)[\"']", re.IGNORECASE)

    def supports(self, source: VendorSpecSource) -> bool:
        parsed = urlparse(source.location)
        return source.kind == "website" and parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)

    def _validate_url(self, source: VendorSpecSource, url: str, *, initial_host: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"Unsupported specification URL: {url}")
        host = _normalized_host(parsed.hostname)
        configured = source.options.get("allowed_hosts") if isinstance(source.options, dict) else None
        allowed_hosts = {
            _normalized_host(item)
            for item in (configured if isinstance(configured, (list, tuple, set)) else [initial_host])
            if _normalized_host(item)
        }
        if host not in allowed_hosts and not any(host.endswith(f".{allowed}") for allowed in allowed_hosts):
            raise ValueError(f"Specification redirect host is not allowed: {host}")
        allow_private = bool(source.options.get("allow_private_hosts")) if isinstance(source.options, dict) else False
        if not allow_private and not _host_is_public(host):
            raise ValueError(f"Specification host does not resolve to a public address: {host}")

    def read(self, source: VendorSpecSource) -> str:
        if not self.supports(source):
            raise ValueError(f"WebsiteSpecReader does not support {source.location!r}")
        initial_host = _normalized_host(urlparse(source.location).hostname or "")
        self._validate_url(source, source.location, initial_host=initial_host)
        timeout = float(source.options.get("timeout_sec", self.timeout_sec)) if isinstance(source.options, dict) else self.timeout_sec
        max_bytes = int(source.options.get("max_bytes", self.max_bytes)) if isinstance(source.options, dict) else self.max_bytes
        user_agent = str(source.options.get("user_agent") or self.user_agent) if isinstance(source.options, dict) else self.user_agent
        if isinstance(source.options, dict) and source.options.get("use_curl"):
            curl_command = [
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--retry",
                "2",
                "--retry-all-errors",
                "--retry-delay",
                "1",
                "--max-time",
                str(max(1, int(timeout // 3))),
                "--max-filesize",
                str(max_bytes),
                "--header",
                "Accept: text/html,application/json,text/plain;q=0.9,*/*;q=0.1",
                "--header",
                "Accept-Encoding: identity",
            ]
            resumable = bool(source.options.get("curl_resume"))
            if not resumable:
                curl_command.insert(1, "--http1.1")
            if not source.options.get("use_default_curl_user_agent"):
                curl_command.extend(("--user-agent", user_agent))
            if resumable:
                descriptor, temporary_name = tempfile.mkstemp(prefix="attas-spec-download-")
                os.close(descriptor)
                Path(temporary_name).unlink(missing_ok=True)
                completed = None
                try:
                    attempts = max(1, min(int(source.options.get("curl_resume_attempts", 8)), 20))
                    for _ in range(attempts):
                        completed = subprocess.run(
                            [*curl_command, "--continue-at", "-", "--output", temporary_name, source.location],
                            check=False,
                            capture_output=True,
                            timeout=timeout + 5,
                        )
                        if Path(temporary_name).is_file() and Path(temporary_name).stat().st_size > max_bytes:
                            raise ValueError(f"Specification response exceeds {max_bytes} bytes")
                        if completed.returncode == 0:
                            break
                    if completed is None or completed.returncode != 0:
                        detail = completed.stderr.decode("utf-8", errors="replace").strip() if completed else ""
                        raise ValueError(f"Specification download failed: {detail or 'curl resume attempts exhausted'}")
                    raw = Path(temporary_name).read_bytes()
                    return raw.decode("utf-8", errors="replace")
                finally:
                    Path(temporary_name).unlink(missing_ok=True)
            curl_command.append(source.location)
            completed = subprocess.run(curl_command, check=False, capture_output=True, timeout=timeout + 5)
            if completed.returncode != 0:
                detail = completed.stderr.decode("utf-8", errors="replace").strip()
                raise ValueError(f"Specification download failed: {detail or f'curl exit {completed.returncode}'}")
            if len(completed.stdout) > max_bytes:
                raise ValueError(f"Specification response exceeds {max_bytes} bytes")
            return completed.stdout.decode("utf-8", errors="replace")
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.1"},
        ) as client:
            response = client.get(source.location)
            response.raise_for_status()
        self._validate_url(source, str(response.url), initial_host=initial_host)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        declared_text = (
            source.media_type in {"application/json", "application/xml"}
            and Path(urlparse(str(response.url)).path).suffix.lower() in _TEXT_SUFFIXES
        )
        extract_pdf = content_type == "application/pdf" and bool(source.options.get("extract_pdf_text"))
        if content_type and not any(content_type.startswith(item) for item in _TEXT_MEDIA_TYPES) and not declared_text and not extract_pdf:
            raise ValueError(f"Unsupported specification content type: {content_type}")
        raw = response.content
        if len(raw) > max_bytes:
            raise ValueError(f"Specification response exceeds {max_bytes} bytes")
        if extract_pdf:
            from pypdf import PdfReader

            document = PdfReader(io.BytesIO(raw))
            return "\n\n".join((page.extract_text() or "") for page in document.pages)
        encoding = response.encoding or "utf-8"
        content = raw.decode(encoding, errors="replace")
        if isinstance(source.options, dict) and source.options.get("follow_json_swagger_links"):
            navigation = json.loads(content)
            links: list[str] = []

            def walk(value) -> None:
                if isinstance(value, dict):
                    if str(value.get("type") or "").upper() == "SWAGGER" and value.get("url"):
                        links.append(urljoin(source.location, str(value["url"])))
                    for item in value.values():
                        walk(item)
                elif isinstance(value, list):
                    for item in value:
                        walk(item)

            walk(navigation)
            path_prefix = str(source.options.get("follow_link_path_prefix") or "")
            links = list(
                dict.fromkeys(
                    link for link in links
                    if not path_prefix or urlparse(link).path.startswith(path_prefix)
                )
            )[: int(source.options.get("follow_link_limit", 250))]
            for link in links:
                self._validate_url(source, link, initial_host=initial_host)

            def fetch_json(link: str):
                with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": user_agent}) as linked_client:
                    linked_response = linked_client.get(link)
                    linked_response.raise_for_status()
                self._validate_url(source, str(linked_response.url), initial_host=initial_host)
                return {"url": link, "document": linked_response.json()}

            with ThreadPoolExecutor(max_workers=min(12, max(1, len(links)))) as executor:
                documents = tuple(executor.map(fetch_json, links))
            combined = json.dumps({"attas_openapi_documents": documents})
            if len(combined.encode("utf-8")) > max_bytes:
                raise ValueError(f"Combined specification response exceeds {max_bytes} bytes")
            return combined
        if isinstance(source.options, dict) and (
            source.options.get("follow_markdown_links") or source.options.get("follow_html_links")
        ):
            if source.options.get("follow_markdown_links"):
                discovered = self._MARKDOWN_LINK_RE.findall(content)
            else:
                discovered = self._HTML_LINK_RE.findall(content)
            path_prefix = str(source.options.get("follow_link_path_prefix") or "")
            links = tuple(
                dict.fromkeys(
                    urljoin(source.location, item)
                    for item in discovered
                    if not path_prefix or urlparse(urljoin(source.location, item)).path.startswith(path_prefix)
                )
            )
            limit = int(source.options.get("follow_link_limit", 250))
            links = links[:limit]
            for link in links:
                self._validate_url(source, urljoin(source.location, link), initial_host=initial_host)

            def fetch(link: str) -> str:
                with httpx.Client(
                    timeout=timeout,
                    follow_redirects=True,
                    headers={"User-Agent": user_agent, "Accept": "text/markdown,text/plain,text/html;q=0.9"},
                ) as linked_client:
                    linked_response = linked_client.get(urljoin(source.location, link))
                    linked_response.raise_for_status()
                self._validate_url(source, str(linked_response.url), initial_host=initial_host)
                if len(linked_response.content) > max_bytes:
                    raise ValueError(f"Linked specification response exceeds {max_bytes} bytes")
                return linked_response.text

            with ThreadPoolExecutor(max_workers=min(12, max(1, len(links)))) as executor:
                linked_content = tuple(executor.map(fetch, links))
            combined = content + "".join(
                f"\n\n<!-- SOURCE URL: {link} -->\n{item}"
                for link, item in zip(links, linked_content)
            )
            if len(combined.encode("utf-8")) > max_bytes:
                raise ValueError(f"Combined specification response exceeds {max_bytes} bytes")
            return combined
        return content


@dataclass(frozen=True)
class GitHubRepositorySpecReader(AbstractSpecReader):
    """Read an approved public GitHub repository as modular OpenAPI or source text."""

    timeout_sec: float = 120.0
    max_bytes: int = 32 * 1024 * 1024

    def supports(self, source: VendorSpecSource) -> bool:
        options = source.options if isinstance(source.options, dict) else {}
        parsed = urlparse(source.location)
        return (
            source.kind == "repository"
            and parsed.scheme == "https"
            and _normalized_host(parsed.hostname or "") == "github.com"
            and bool(options.get("github_openapi_file") or options.get("github_text_globs"))
        )

    @staticmethod
    def _resolve_external_refs(value, *, root: Path, current_file: Path, resolving: tuple[str, ...] = ()):
        if isinstance(value, list):
            return [
                GitHubRepositorySpecReader._resolve_external_refs(
                    item, root=root, current_file=current_file, resolving=resolving
                )
                for item in value
            ]
        if not isinstance(value, dict):
            return value
        reference = str(value.get("$ref") or "")
        if reference and not reference.startswith("#/"):
            file_part, _, fragment = reference.partition("#")
            target_file = (current_file.parent / file_part).resolve()
            if root not in target_file.parents and target_file != root:
                raise ValueError("OpenAPI reference escapes the approved repository root")
            marker = f"{target_file}#{fragment}"
            if marker in resolving:
                return {"type": "object", "additionalProperties": True}
            import yaml

            target = yaml.safe_load(target_file.read_text(encoding="utf-8"))
            if fragment.startswith("/"):
                for part in fragment[1:].split("/"):
                    part = part.replace("~1", "/").replace("~0", "~")
                    target = target[part]
            resolved = GitHubRepositorySpecReader._resolve_external_refs(
                target,
                root=root,
                current_file=target_file,
                resolving=(*resolving, marker),
            )
            siblings = {key: item for key, item in value.items() if key != "$ref"}
            if siblings and isinstance(resolved, dict):
                return {**resolved, **siblings}
            return resolved
        return {
            key: GitHubRepositorySpecReader._resolve_external_refs(
                item, root=root, current_file=current_file, resolving=resolving
            )
            for key, item in value.items()
        }

    def read(self, source: VendorSpecSource) -> str:
        if not self.supports(source):
            raise ValueError(f"GitHubRepositorySpecReader does not support {source.location!r}")
        options = source.options if isinstance(source.options, dict) else {}
        timeout = float(options.get("timeout_sec", self.timeout_sec))
        max_bytes = int(options.get("max_bytes", self.max_bytes))
        with tempfile.TemporaryDirectory(prefix="attas-provider-spec-") as temp_dir:
            root = Path(temp_dir) / "repository"
            sparse_patterns = tuple(
                str(item).strip()
                for item in options.get("github_sparse_patterns") or ()
                if str(item).strip()
            )
            clone_command = ["git", "clone", "--quiet", "--depth", "1"]
            if sparse_patterns:
                clone_command.extend(("--filter=blob:none", "--no-checkout"))
            clone_command.extend((source.location, str(root)))
            completed = subprocess.run(
                clone_command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if completed.returncode != 0:
                raise ValueError(f"Specification repository clone failed: {completed.stderr.strip()}")
            if sparse_patterns:
                for command in (
                    ["git", "-C", str(root), "sparse-checkout", "init", "--no-cone"],
                    ["git", "-C", str(root), "sparse-checkout", "set", "--no-cone", *sparse_patterns],
                    ["git", "-C", str(root), "checkout", "--quiet"],
                ):
                    completed = subprocess.run(
                        command,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                    )
                    if completed.returncode != 0:
                        raise ValueError(
                            f"Specification repository sparse checkout failed: {completed.stderr.strip()}"
                        )
            root = root.resolve()
            openapi_file = str(options.get("github_openapi_file") or "").strip()
            if openapi_file:
                import yaml

                path = (root / openapi_file).resolve()
                if root not in path.parents or not path.is_file():
                    raise ValueError("Configured OpenAPI file is outside or missing from the repository")
                document = yaml.safe_load(path.read_text(encoding="utf-8"))
                content = yaml.safe_dump(
                    self._resolve_external_refs(document, root=root, current_file=path),
                    sort_keys=False,
                )
            else:
                files: list[Path] = []
                for pattern in options.get("github_text_globs") or ():
                    files.extend(Path(item) for item in glob.glob(str(root / str(pattern)), recursive=True))
                files = sorted({path.resolve() for path in files if path.is_file() and root in path.resolve().parents})
                content = "\n\n".join(
                    f"<!-- SOURCE FILE: {path.relative_to(root)} -->\n"
                    f"{path.read_text(encoding='utf-8', errors='replace')}"
                    for path in files
                )
            if len(content.encode("utf-8")) > max_bytes:
                raise ValueError(f"Specification repository content exceeds {max_bytes} bytes")
            return content


@dataclass(frozen=True)
class DocumentSpecReader(AbstractSpecReader):
    """Read approved local JSON, text, HTML, PDF, or DOCX documentation."""

    allowed_roots: tuple[Path, ...] = ()
    max_bytes: int = 16 * 1024 * 1024
    max_pdf_pages: int = 2_000

    def supports(self, source: VendorSpecSource) -> bool:
        suffix = Path(source.location).suffix.lower()
        return source.kind == "document" and suffix in {*_TEXT_SUFFIXES, ".pdf", ".docx"}

    def _roots_for(self, source: VendorSpecSource) -> tuple[Path, ...]:
        configured = source.options.get("allowed_roots") if isinstance(source.options, dict) else None
        roots: list[Path] = [Path(item).expanduser().resolve() for item in self.allowed_roots]
        if isinstance(configured, Iterable) and not isinstance(configured, (str, bytes)):
            roots.extend(Path(str(item)).expanduser().resolve() for item in configured)
        path = Path(source.location).expanduser().resolve()
        return tuple(dict.fromkeys(roots or [path.parent]))

    def _resolve_path(self, source: VendorSpecSource) -> Path:
        path = Path(source.location).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        if not any(path == root or root in path.parents for root in self._roots_for(source)):
            raise ValueError(f"Specification document is outside approved roots: {path}")
        max_bytes = int(source.options.get("max_bytes", self.max_bytes)) if isinstance(source.options, dict) else self.max_bytes
        if path.stat().st_size > max_bytes:
            raise ValueError(f"Specification document exceeds {max_bytes} bytes")
        return path

    def read(self, source: VendorSpecSource) -> str:
        if not self.supports(source):
            raise ValueError(f"DocumentSpecReader does not support {source.location!r}")
        path = self._resolve_path(source)
        suffix = path.suffix.lower()
        if suffix in _TEXT_SUFFIXES:
            return path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            if len(reader.pages) > self.max_pdf_pages:
                raise ValueError(f"Specification PDF exceeds {self.max_pdf_pages} pages")
            return "\n\n".join((page.extract_text() or "") for page in reader.pages)
        if suffix == ".docx":
            from docx import Document

            document = Document(str(path))
            return "\n".join(paragraph.text for paragraph in document.paragraphs)
        raise ValueError(f"Unsupported specification document: {path}")
