"""Data Sources for public open-data APIs (vendors.py), on the shared provider runtime.

Each source's catalog is built from its curated definition, so it needs no
documentation download: `build_snapshot(vendor_id)` is deterministic and the
committed catalog under catalogs/<vendor_id>/ is the same snapshot. Requests run
through the shared provider executor with the vendor's execution policy, which
this module registers for its own vendors on import.

Sources whose vendor issues personal keys (Taiwan CWA and MOENV, the street map
tile providers and Google Maps) publish only where the key goes; a user's
Terminal sends its own key to the vendor directly. An endpoint may name its HTTP
method (`method`, GET by default) and take JSON body fields (`location: body`).
"""
from __future__ import annotations

import base64
import copy
import dataclasses
import hashlib
import json
from dataclasses import asdict
from typing import Any, Dict, Mapping, Tuple

from ..scaffold.connections import PROVIDER_CONNECTION_PROFILES, PROVIDER_EXECUTION_POLICIES
from ..scaffold.models import (EndpointExecutionResult, EndpointParameterSpec, EndpointSpec, EndpointSpecSnapshot,
                               VendorSpecSource)
from ..scaffold.sources import FinancialProviderDataSource
from .vendors import VENDORS

PARSER_VERSION = 'OpenDataCatalog:1'
# The catalog's date: when these definitions were last checked against the vendors' documentation.
REVIEWED_AT = '2026-11-14T00:00:00+00:00'
SYMBOLOGY = {'contract_version': 'source-symbology/1', 'status': 'not_applicable', 'input_types': [], 'output_types': [],
             'notes': 'Uses country, region, area and dataset parameters; no instrument symbology.', 'evidence': []}


def register() -> None:
    """Give the shared provider executor each open-data vendor's policy (idempotent)."""
    for vendor_id, vendor in VENDORS.items():
        PROVIDER_EXECUTION_POLICIES.setdefault(vendor_id, copy.deepcopy(vendor['policy']))
        definition = vendor['definition']
        PROVIDER_CONNECTION_PROFILES.setdefault(vendor_id, {
            'credential_label': definition['provider'] + (' API key' if vendor.get('key_slot') else ' credential (not required)'),
            'required': bool(definition.get('authentication_required')),
            'direct_access_enabled': bool(vendor.get('key_slot')),
        })


register()


def spec_source(vendor_id: str) -> VendorSpecSource:
    definition = VENDORS[vendor_id]['definition']
    return VendorSpecSource(source_id=vendor_id.replace('_', '-') + '-official-api', vendor_id=vendor_id, kind='document',
                            location=definition['documentation'], media_type='text/html',
                            description=f"Official {definition['provider']} API documentation; endpoints curated from it.",
                            options={'curated': True})


def _authentication(vendor: Mapping[str, Any]) -> Dict[str, Any]:
    if vendor['definition'].get('authentication_required'):
        # The source publishes the key slot from its policy (FinancialProviderDataSource._published_authentication).
        return {'required': True, 'scheme': 'delegated_connection', 'provider_scheme': 'api_key',
                'credential_reference': 'connection_id'}
    return {'required': False, 'scheme': 'public_provider'}


def _endpoint(vendor_id: str, vendor: Mapping[str, Any], row: Mapping[str, Any]) -> EndpointSpec:
    definition = vendor['definition']
    keyed = bool(definition.get('authentication_required'))
    return EndpointSpec(
        endpoint_id=f"{vendor_id}.{row['identity']}", vendor_id=vendor_id, name=row['name'],
        description=row['description'], category=row['category'], transport='https', operation=row['operation'],
        parameters=tuple(EndpointParameterSpec(**parameter) for parameter in row['parameters']),
        response_schema=copy.deepcopy(row['response']), authentication=_authentication(vendor),
        cost={'model': 'provider_terms' if keyed else 'free', 'amount': 0, 'currency': 'USD', 'unit': 'request', 'notes': [],
              'provider_cost': definition.get('provider_cost') or (
                  'Free with a personal key from the provider; the provider sets request limits.' if keyed
                  else 'Free public API; the provider sets fair-use limits.')},
        quality={'status': 'provider_documented',
                 'notes': ['Fields and parameters are curated from the provider documentation linked here.',
                           'Licence: ' + definition['license']]},
        documentation_url=row['documentation_url'], executable=True,
        extensions={'attas.http_method': row.get('method', 'GET'), 'attas.schema_provenance': 'curated_provider_documentation',
                    'attas.specification_only': False, 'attas.vendor_version': '',
                    **({'attas.media_type': row['media_type']} if row.get('media_type') else {}),
                    # An endpoint served from another of the vendor's hosts (one of the policy's allowed hosts).
                    **({'attas.base_url': row['base_url']} if row.get('base_url') else {})})


def build_snapshot(vendor_id: str) -> EndpointSpecSnapshot:
    """The vendor's catalog from its curated definition; the same input always gives the same snapshot."""
    vendor = VENDORS[vendor_id]
    endpoints = tuple(_endpoint(vendor_id, vendor, row) for row in vendor['endpoints'])
    digest = hashlib.sha256(json.dumps([asdict(e) for e in endpoints], sort_keys=True, default=list).encode()).hexdigest()
    return EndpointSpecSnapshot(vendor_id=vendor_id, retrieved_at=REVIEWED_AT, content_hash=digest,
                                parser_version=PARSER_VERSION, sources=(spec_source(vendor_id),), endpoints=endpoints)


class OpenDataSource(FinancialProviderDataSource):
    """A free public API, described by its curated definition in vendors.py."""

    VENDOR_ID = ''

    def __init__(self, config=None, **kwargs):
        super().__init__(config=config, **kwargs)
        # The catalog comes from the definition, never from a documentation download.
        self.spec_ingestion.parsers = ()

    def source_profile(self) -> Mapping[str, Any]:
        definition = VENDORS[self.VENDOR_ID]['definition']
        configured = self._config_data.get('source_profile') if isinstance(self._config_data.get('source_profile'), Mapping) else {}
        keyed = bool(definition.get('authentication_required'))
        profile = {
            'source_id': self.VENDOR_ID, 'source_name': definition['source_name'], 'provider': definition['provider'],
            'description': definition['description'],
            # The credit the vendor asks for wherever its data is shown, such as under a map.
            **({'attribution': definition['attribution']} if definition.get('attribution') else {}),
            # The terms the data comes under, so a view can show them with the credit (licence check, 2026-11-15).
            **({'license': definition['license']} if definition.get('license') else {}),
            'symbology': {**copy.deepcopy(SYMBOLOGY), 'evidence': [f'attas/data_agent_network/catalogs/{self.VENDOR_ID}/manifest.json']},
            'connectivity': {'status': 'configured', 'transport': 'https', 'pulse_name': 'data_availability',
                             'authentication': definition['authentication'],
                             'configuration_required': [definition['provider'] + ' API key in your Terminal'] if keyed else [],
                             'notes': ['Endpoints are curated from the provider documentation: ' + definition['documentation']]},
            'coverage': {'asset_classes': list(definition.get('coverage') or [])},
            'entitlement': {'status': 'provider-terms', 'allowed_output_forms': ['request-scoped provider response'],
                            'notes': ['Licence: ' + definition['license']]},
        }
        return {**profile, **copy.deepcopy(dict(configured)),
                'symbology': copy.deepcopy(dict(configured.get('symbology') or profile['symbology']))}

    def spec_sources(self) -> Tuple[VendorSpecSource, ...]:
        return (spec_source(self.VENDOR_ID),)

    def refresh_spec_catalog(self, ingestion):
        snapshot = build_snapshot(self.VENDOR_ID)
        self.spec_repository.save(snapshot)
        self.spec_snapshot = snapshot
        if hasattr(self, 'data_catalog'):
            self.data_catalog = self._catalog_entries(snapshot)
            self._apply_source_card()
        return snapshot


class USGSEarthquakesDataSource(OpenDataSource):
    VENDOR_ID = 'usgs_earthquakes'


class OurWorldInDataDataSource(OpenDataSource):
    VENDOR_ID = 'our_world_in_data'


class EurostatDataSource(OpenDataSource):
    VENDOR_ID = 'eurostat'


class WHOHealthDataSource(OpenDataSource):
    VENDOR_ID = 'who_gho'


class TaiwanCWADataSource(OpenDataSource):
    VENDOR_ID = 'taiwan_cwa'
    PUBLISHES_KEY_SLOT = True


class TaiwanMOENVDataSource(OpenDataSource):
    VENDOR_ID = 'taiwan_moenv'
    PUBLISHES_KEY_SLOT = True


class NASAGIBSDataSource(OpenDataSource):
    VENDOR_ID = 'nasa_gibs'


class EOXCloudlessDataSource(OpenDataSource):
    VENDOR_ID = 'eox_cloudless'


def tile_problem(parameters, deepest):
    """Why a z/x/y tile address names no tile (deeper than the map goes, or off its grid), or '' when it is one. Such a
    tile is refused here, without asking the server for it."""
    try:
        zoom, column, row = (int(parameters[name]) for name in ('TileMatrix', 'TileCol', 'TileRow'))
    except (KeyError, TypeError, ValueError):
        return ''  # left to the executor's own parameter checks
    if zoom < 0 or zoom > deepest:
        return f'This map has tiles from zoom 0 to {deepest}.'
    if not (0 <= column < 2 ** zoom and 0 <= row < 2 ** zoom):
        return f'Zoom {zoom} has no tile at column {column}, row {row}.'
    return ''


class OpenStreetMapDataSource(OpenDataSource):
    """OpenStreetMap's standard map and OpenTopoMap as tiles, with no key.

    Their tile servers are donated and ask for light use (OpenStreetMap's Tile Usage Policy), so this source runs on
    users' own computers (scripts/run_local_openstreetmap.py), never on a shared site. Given `tile_cache`
    ({'directory', optional 'max_bytes' and 'per_minute'}), it keeps the tiles it fetched in that folder and asks the
    servers as the policy says (tile_cache.py); it never asks for a tile deeper than a map goes or off its grid."""
    VENDOR_ID = 'openstreetmap'
    # The deepest zoom each server draws (its endpoint's description).
    DEEPEST = {'openstreetmap.standard_tile': 19, 'openstreetmap.topographic_tile': 17}
    BUSY = ('OpenStreetMap asks for light use: too many new map tiles were asked for in the last minute. '
            'Wait a moment and try again.')

    def __init__(self, config=None, **kwargs):
        super().__init__(config=config, **kwargs)
        cache = self._config_data.get('tile_cache')
        if self._http_client is None and isinstance(cache, Mapping) and cache.get('directory'):
            from .tile_cache import TileCache
            self._http_client = TileCache(cache['directory'], **{key: int(cache[key]) for key in ('max_bytes', 'per_minute')
                                                                 if cache.get(key) is not None})

    def execute_endpoint(self, endpoint, parameters, auth_context):
        problem = tile_problem(parameters or {}, self.DEEPEST.get(endpoint.endpoint_id, 19))
        if problem:
            return EndpointExecutionResult(status='failed', endpoint_id=endpoint.endpoint_id, data_schema=endpoint.response_schema,
                                           cost=endpoint.cost, error=problem)
        result = super().execute_endpoint(endpoint, parameters, auth_context)
        if result.status == 'rate_limited':
            result = dataclasses.replace(result, error=self.BUSY)
        return result


def _varint(data, at):
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, at
        shift += 7


def keep_layers(tile, names):
    """A Mapbox Vector Tile with only the named layers. A tile is a list of length-delimited layers (field 3), each
    naming itself in its field 1; the layers kept are copied byte for byte, nothing else is decoded."""
    out, at = bytearray(), 0
    while at < len(tile):
        start = at
        key, at = _varint(tile, at)
        if key & 7 != 2:
            raise ValueError('The vector tile has an unexpected field.')
        size, at = _varint(tile, at)
        body, at = tile[at:at + size], at + size
        name, inner = '', 0
        while inner < len(body):
            field, inner = _varint(body, inner)
            if field & 7 == 2:
                length, inner = _varint(body, inner)
                if field >> 3 == 1:
                    name = body[inner:inner + length].decode('utf-8', 'replace')
                    break
                inner += length
            elif field & 7 == 0:
                _, inner = _varint(body, inner)
            else:
                inner += 8 if field & 7 == 1 else 4
        if key >> 3 != 3 or name in names:
            out += tile[start:at]
    return bytes(out)


class VersaTilesDataSource(OpenDataSource):
    VENDOR_ID = 'versatiles'

    def execute_endpoint(self, endpoint, parameters, auth_context):
        """A VersaTiles tile carries every layer; `layers`, this source's own parameter, keeps only those named, so a
        dense city tile (over 600 KB) stays small enough to pass on."""
        parameters = dict(parameters or {})
        wanted = {name.strip() for name in str(parameters.pop('layers', '') or '').split(',') if name.strip()}
        result = super().execute_endpoint(endpoint, parameters, auth_context)
        data = result.data if isinstance(result.data, dict) else {}
        if wanted and result.status == 'completed' and isinstance(data.get('content_base64'), str):
            try:
                tile = keep_layers(base64.b64decode(data['content_base64']), wanted)
            except (ValueError, IndexError):
                return result  # not a tile this reader understands: passed on whole
            result = dataclasses.replace(result, data={**data, 'content_base64': base64.b64encode(tile).decode('ascii')})
        return result


class StadiaMapsDataSource(OpenDataSource):
    VENDOR_ID = 'stadia_maps'
    PUBLISHES_KEY_SLOT = True


class MapTilerDataSource(OpenDataSource):
    VENDOR_ID = 'maptiler'
    PUBLISHES_KEY_SLOT = True


class ThunderforestDataSource(OpenDataSource):
    VENDOR_ID = 'thunderforest'
    PUBLISHES_KEY_SLOT = True


class GoogleMapDataSource(OpenDataSource):
    """Google Maps tiles (Map Tiles API), read with each user's own key: a session first, then its tiles."""
    VENDOR_ID = 'google_maps'
    PUBLISHES_KEY_SLOT = True


SOURCES = {cls.VENDOR_ID: cls for cls in (USGSEarthquakesDataSource, OurWorldInDataDataSource, EurostatDataSource,
                                          WHOHealthDataSource, TaiwanCWADataSource, TaiwanMOENVDataSource,
                                          NASAGIBSDataSource, EOXCloudlessDataSource, OpenStreetMapDataSource,
                                          VersaTilesDataSource, StadiaMapsDataSource, MapTilerDataSource,
                                          ThunderforestDataSource, GoogleMapDataSource)}
