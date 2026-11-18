"""Vendor specification ingestion orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Iterable, Tuple

from .models import EndpointSpec, EndpointSpecSnapshot, VendorSpecSource
from .parsers import AbstractSpecParser
from .readers import AbstractSpecReader
from .repository import AbstractEndpointSpecRepository


@dataclass
class VendorSpecIngestionService:
    """Coordinate readers, vendor parsers, validation, and snapshot storage."""

    readers: Tuple[AbstractSpecReader, ...]
    parsers: Tuple[AbstractSpecParser, ...]
    repository: AbstractEndpointSpecRepository

    def select_reader(self, source: VendorSpecSource) -> AbstractSpecReader:
        matches = [reader for reader in self.readers if reader.supports(source)]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one specification reader for {source.source_id!r}; found {len(matches)}"
            )
        return matches[0]

    def select_parser(self, source: VendorSpecSource) -> AbstractSpecParser:
        matches = [parser for parser in self.parsers if parser.supports(source)]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one specification parser for {source.source_id!r}; found {len(matches)}"
            )
        return matches[0]

    @staticmethod
    def _content_hash(items: Iterable[tuple[VendorSpecSource, str]]) -> str:
        digest = hashlib.sha256()
        for source, content in items:
            digest.update(source.source_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content.encode("utf-8", errors="replace"))
            digest.update(b"\0")
        return digest.hexdigest()

    def refresh(self, source: VendorSpecSource) -> EndpointSpecSnapshot:
        reader = self.select_reader(source)
        parser = self.select_parser(source)
        content = reader.read(source)
        endpoints = parser.parse(source, content)
        endpoint_ids = [endpoint.endpoint_id for endpoint in endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError(f"Parser produced duplicate endpoint IDs for {source.vendor_id}")
        snapshot = EndpointSpecSnapshot(
            vendor_id=source.vendor_id,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            content_hash=self._content_hash(((source, content),)),
            parser_version=f"{parser.__class__.__name__}:{parser.VERSION}",
            sources=(source,),
            endpoints=endpoints,
        )
        self.repository.save(snapshot)
        return snapshot

    def refresh_many(self, sources: Iterable[VendorSpecSource]) -> Tuple[EndpointSpecSnapshot, ...]:
        grouped: dict[str, list[VendorSpecSource]] = {}
        for source in sources:
            grouped.setdefault(source.vendor_id, []).append(source)
        snapshots: list[EndpointSpecSnapshot] = []
        for vendor_id, vendor_sources in grouped.items():
            read_items: list[tuple[VendorSpecSource, str]] = []
            endpoints: dict[str, EndpointSpec] = {}
            parser_versions: list[str] = []
            warnings: list[str] = []
            for source in vendor_sources:
                try:
                    reader = self.select_reader(source)
                    parser = self.select_parser(source)
                    content = reader.read(source)
                    parsed = parser.parse(source, content)
                    read_items.append((source, content))
                    parser_versions.append(f"{parser.__class__.__name__}:{parser.VERSION}")
                    for endpoint in parsed:
                        if endpoint.endpoint_id in endpoints and endpoints[endpoint.endpoint_id] != endpoint:
                            warnings.append(f"Duplicate endpoint merged: {endpoint.endpoint_id}")
                        endpoints[endpoint.endpoint_id] = endpoint
                except Exception as exc:
                    warnings.append(f"{source.source_id}: {exc}")
            if not endpoints or not read_items:
                raise ValueError(f"No endpoint specifications could be ingested for {vendor_id}: {'; '.join(warnings)}")
            snapshot = EndpointSpecSnapshot(
                vendor_id=vendor_id,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                content_hash=self._content_hash(read_items),
                parser_version=",".join(dict.fromkeys(parser_versions)),
                sources=tuple(item[0] for item in read_items),
                endpoints=tuple(endpoints.values()),
                warnings=tuple(dict.fromkeys(warnings)),
            )
            self.repository.save(snapshot)
            snapshots.append(snapshot)
        return tuple(snapshots)
