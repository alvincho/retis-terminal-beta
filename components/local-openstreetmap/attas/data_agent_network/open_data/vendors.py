"""Public open-data APIs served as Data Sources, each described from its official documentation.

USGS, Our World in Data, Eurostat, WHO, NASA GIBS, EOX, OpenStreetMap and
VersaTiles need no key. Taiwan's Central Weather Administration and Ministry of
Environment, the street map tile providers and Google Maps Platform issue personal
keys, which stay in each user's Terminal (the source only publishes where the key
goes); Google bills the user's own project for its tiles.

The endpoint lists are curated from each vendor's documentation (linked per
vendor) rather than parsed: the vendors document their replies in prose or not
at all, and the Data Consultant needs field-level response schemas. Each
endpoint keeps the vendor's own parameter and field names.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

STRING = {'type': 'string'}
INTEGER = {'type': 'integer'}
NUMBER = {'type': 'number'}


def p(name, description, schema=None, *, location='query', required=False, default=None, examples=()):
    """One documented request parameter."""
    return {'name': name, 'location': location, 'description': description, 'required': required,
            'schema': dict(schema or STRING), 'default': default, 'examples': tuple(examples)}


def obj(properties, description='', required=()):
    out = {'type': 'object', 'properties': properties}
    if description:
        out['description'] = description
    if required:
        out['required'] = list(required)
    return out


def arr(items, description=''):
    out = {'type': 'array', 'items': items}
    if description:
        out['description'] = description
    return out


def leaf(kind, description, **extra):
    return {'type': kind, 'description': description, **extra}


# A reply that is not JSON (CSV, for example) reaches the caller as text with its media type.
TEXT_REPLY = obj({'content': leaf('string', 'The reply body as text.'),
                  'content_type': leaf('string', 'Media type of the reply, e.g. text/csv.')},
                 'A text reply, as the Data Source passes it on.', ('content', 'content_type'))


# --- USGS earthquake catalog ---------------------------------------------------------------
USGS_DOCS = 'https://earthquake.usgs.gov/fdsnws/event/1/'
USGS_FEATURE = obj({
    'type': leaf('string', 'GeoJSON object type, Feature.'),
    'id': leaf('string', 'USGS event identifier, e.g. us7000twdk.'),
    'properties': obj({
        'mag': leaf('number', 'Event magnitude, in the magnitude type magType.'),
        'magType': leaf('string', 'Method or algorithm used to compute the magnitude, e.g. mb, ml, mw, mww.'),
        'place': leaf('string', 'Textual description of the named geographic region near the event.'),
        'time': leaf('integer', 'Event origin time, milliseconds since the Unix epoch (UTC).'),
        'updated': leaf('integer', 'Time the event was most recently updated, milliseconds since the Unix epoch.'),
        'tz': leaf(['integer', 'null'], 'Deprecated timezone offset from UTC in minutes at the epicentre.'),
        'url': leaf('string', 'Link to the USGS event page.'),
        'detail': leaf('string', 'Link to the GeoJSON detail feed for this event.'),
        'felt': leaf(['integer', 'null'], 'Number of felt reports submitted to Did You Feel It?'),
        'cdi': leaf(['number', 'null'], 'Maximum reported community intensity (Did You Feel It?).'),
        'mmi': leaf(['number', 'null'], 'Maximum estimated instrumental intensity (ShakeMap).'),
        'alert': leaf(['string', 'null'], 'PAGER alert level: green, yellow, orange or red.'),
        'status': leaf('string', 'automatic or reviewed by a seismologist.'),
        'tsunami': leaf('integer', '1 for large events in oceanic regions, 0 otherwise; not a tsunami warning.'),
        'sig': leaf('integer', 'Significance of the event, 0 to 1000, from magnitude, felt reports and impact.'),
        'net': leaf('string', 'Identifier of the preferred contributing network.'),
        'code': leaf('string', 'Identifying code assigned by the source network.'),
        'ids': leaf('string', 'Comma-separated list of event identifiers associated with the event.'),
        'sources': leaf('string', 'Comma-separated list of network contributors.'),
        'types': leaf('string', 'Comma-separated list of product types associated with the event.'),
        'nst': leaf(['integer', 'null'], 'Number of seismic stations used to determine the location.'),
        'dmin': leaf(['number', 'null'], 'Horizontal distance from the epicentre to the nearest station, in degrees.'),
        'rms': leaf(['number', 'null'], 'Root-mean-square travel-time residual, in seconds.'),
        'gap': leaf(['number', 'null'], 'Largest azimuthal gap between adjacent stations, in degrees.'),
        'type': leaf('string', 'Type of seismic event, e.g. earthquake, quarry blast, explosion.'),
        'title': leaf('string', 'Title of the event, e.g. "M 4.2 - 76 km ENE of Donggang, Taiwan".'),
    }),
    'geometry': obj({
        'type': leaf('string', 'GeoJSON geometry type, Point.'),
        'coordinates': arr(NUMBER, 'Longitude and latitude in WGS84 decimal degrees, then depth in kilometres.'),
    }),
}, 'One earthquake as a GeoJSON Feature.')
USGS_COLLECTION = obj({
    'type': leaf('string', 'GeoJSON object type, FeatureCollection.'),
    'metadata': obj({
        'generated': leaf('integer', 'Time the reply was generated, milliseconds since the Unix epoch.'),
        'url': leaf('string', 'URL of the request.'),
        'title': leaf('string', 'Title of the feed.'),
        'status': leaf('integer', 'HTTP status of the reply.'),
        'api': leaf('string', 'Version of the event web service.'),
        'count': leaf('integer', 'Number of events in the reply.'),
    }),
    'bbox': arr(NUMBER, 'Bounding box of the events: min longitude, min latitude, min depth, max longitude, max latitude, max depth.'),
    'features': arr(USGS_FEATURE, 'The earthquakes.'),
}, 'Earthquakes as a GeoJSON FeatureCollection.')
USGS_SEARCH = [
    p('starttime', 'Earliest origin time, ISO 8601 (UTC unless a zone is given); defaults to 30 days ago.'),
    p('endtime', 'Latest origin time, ISO 8601; defaults to now.'),
    p('updatedafter', 'Only events updated after this ISO 8601 time.'),
    p('minlatitude', 'Southern limit of a rectangle, -90 to 90 degrees.', NUMBER),
    p('maxlatitude', 'Northern limit of a rectangle, -90 to 90 degrees.', NUMBER),
    p('minlongitude', 'Western limit of a rectangle, -360 to 360 degrees; below -180 crosses the date line.', NUMBER),
    p('maxlongitude', 'Eastern limit of a rectangle, -360 to 360 degrees; above 180 crosses the date line.', NUMBER),
    p('latitude', 'Latitude of a circle centre, -90 to 90 degrees.', NUMBER),
    p('longitude', 'Longitude of a circle centre, -180 to 180 degrees.', NUMBER),
    p('maxradiuskm', 'Radius of the circle in kilometres, up to 20001.6.', NUMBER),
    p('maxradius', 'Radius of the circle in degrees, up to 180.', NUMBER),
    p('mindepth', 'Shallowest depth in kilometres, -100 to 1000.', NUMBER),
    p('maxdepth', 'Deepest depth in kilometres, -100 to 1000.', NUMBER),
    p('minmagnitude', 'Smallest magnitude.', NUMBER),
    p('maxmagnitude', 'Largest magnitude.', NUMBER),
    p('eventtype', 'Event type, e.g. earthquake; comma-separated for several.'),
    p('alertlevel', 'Only events with this PAGER alert level.', {'type': 'string', 'enum': ['green', 'yellow', 'orange', 'red']}),
    p('reviewstatus', 'Only automatic or only reviewed events.', {'type': 'string', 'enum': ['automatic', 'reviewed']}),
    p('mincdi', 'Smallest maximum community intensity.', NUMBER),
    p('minfelt', 'Fewest felt reports.', INTEGER),
    p('minmmi', 'Smallest maximum instrumental intensity.', NUMBER),
    p('minsig', 'Smallest significance.', INTEGER),
    p('producttype', 'Only events with this product type, e.g. shakemap or losspager.'),
    p('catalog', 'Only events from this catalog.'),
    p('contributor', 'Only events contributed by this network.'),
]
USGS = {
    'definition': {
        'source_name': 'USGS Earthquakes Data Source', 'provider': 'U.S. Geological Survey',
        'description': 'The USGS Comprehensive Catalog (ComCat) of earthquakes worldwide: searches by time, area, '
                       'depth and magnitude, event counts and the real-time summary feeds, as GeoJSON.',
        'coverage': ['earthquakes', 'seismic events', 'global'],
        'authentication': 'No key is required; USGS data are public domain.',
        'license': 'Public domain (U.S. Government work).',
        'attribution': 'Credit: U.S. Geological Survey',
        'documentation': USGS_DOCS,
    },
    'policy': {'base_url': 'https://earthquake.usgs.gov', 'required': False, 'defaults': {'format': 'geojson'},
               'allowed_base_hosts': ('earthquake.usgs.gov',)},
    'endpoints': [
        {'identity': 'event_query', 'name': 'Earthquake search', 'category': 'Earthquake catalog',
         'operation': '/fdsnws/event/1/query', 'documentation_url': USGS_DOCS,
         'description': 'Earthquakes matching a time window, a rectangle or circle, depth and magnitude, newest first by '
                        'default; at most 20,000 events per request.',
         'parameters': [
             p('format', 'Reply format; this source asks for geojson.', {'type': 'string', 'enum': ['geojson']}, default='geojson'),
             *USGS_SEARCH,
             p('limit', 'Most events to return, 1 to 20000.', {'type': 'integer', 'minimum': 1, 'maximum': 20000}),
             p('offset', 'Return events starting at this position (1 is the first).', {'type': 'integer', 'minimum': 1}),
             p('orderby', 'Sort order.', {'type': 'string', 'enum': ['time', 'time-asc', 'magnitude', 'magnitude-asc']}),
             p('eventid', 'One event by its identifier.'),
         ],
         'response': USGS_COLLECTION},
        {'identity': 'event_count', 'name': 'Earthquake count', 'category': 'Earthquake catalog',
         'operation': '/fdsnws/event/1/count', 'documentation_url': USGS_DOCS,
         'description': 'How many earthquakes match the same filters as the search, without the events.',
         'parameters': [p('format', 'Reply format; this source asks for geojson.', {'type': 'string', 'enum': ['geojson']},
                          default='geojson'), *USGS_SEARCH],
         'response': obj({'count': leaf('integer', 'Number of matching events.'),
                          'maxAllowed': leaf('integer', 'Most events one search may return.')})},
        {'identity': 'summary_feed', 'name': 'Real-time earthquake feed', 'category': 'Earthquake feeds',
         'operation': '/earthquakes/feed/v1.0/summary/{feed}.geojson',
         'documentation_url': 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php',
         'description': 'A real-time summary feed, updated every minute: significant, M4.5+, M2.5+, M1.0+ or all '
                        'earthquakes of the past hour, day, week or month.',
         'parameters': [p('feed', 'Magnitude group and period, e.g. 4.5_day or significant_month.',
                          {'type': 'string', 'enum': [f'{group}_{period}' for group in ('significant', '4.5', '2.5', '1.0', 'all')
                                                      for period in ('hour', 'day', 'week', 'month')]},
                          location='path', required=True)],
         'response': USGS_COLLECTION},
    ],
}


# --- Our World in Data -----------------------------------------------------------------------
OWID_DOCS = 'https://docs.owid.io/projects/etl/api/chart-api/'
OWID = {
    'definition': {
        'source_name': 'Our World in Data Data Source', 'provider': 'Our World in Data',
        'description': 'The data behind every Our World in Data chart, by country and year: health, population, '
                       'energy, emissions, economy, education and more, with chart metadata and sources.',
        'coverage': ['global development indicators', 'countries', 'regions'],
        'authentication': 'No key is required; charts are licensed CC BY 4.0 unless a chart says otherwise.',
        'license': 'CC BY 4.0 (Our World in Data); third-party data keep their own licences.',
        'attribution': 'Our World in Data and its original data sources (CC BY 4.0)',
        'documentation': OWID_DOCS,
    },
    'policy': {'base_url': 'https://ourworldindata.org', 'required': False, 'allowed_base_hosts': ('ourworldindata.org',)},
    'endpoints': [
        {'identity': 'chart_csv', 'name': 'Chart data (CSV)', 'category': 'Chart data',
         'operation': '/grapher/{slug}.csv', 'documentation_url': OWID_DOCS,
         'description': 'The data of one chart as CSV: entity (country or region), code (ISO 3166-1 alpha-3, or an '
                        'OWID_ code for aggregates), year, then one column per indicator.',
         'parameters': [
             p('slug', 'Chart name from its URL, e.g. life-expectancy.', location='path', required=True,
               examples=('life-expectancy', 'co-emissions-per-capita')),
             p('v', 'API version; 1.', default='1'),
             p('csvType', 'full for every entity and year, filtered for the chart view the other parameters select.',
               {'type': 'string', 'enum': ['full', 'filtered']}),
             p('useColumnShortNames', 'true names indicator columns by their short names.',
               {'type': 'string', 'enum': ['true', 'false']}),
             p('time', 'Chart view year or range, as in the chart URL: latest, a year, or start..end.'),
             p('tab', 'Chart view tab, as in the chart URL; map shows every country.',
               {'type': 'string', 'enum': ['chart', 'map', 'table']}),
             p('country', 'Chart view selection, as in the chart URL: codes joined by ~, e.g. ~USA~TWN.'),
         ],
         'response': TEXT_REPLY, 'media_type': 'text/csv'},
        {'identity': 'chart_metadata', 'name': 'Chart metadata', 'category': 'Chart data',
         'operation': '/grapher/{slug}.metadata.json', 'documentation_url': OWID_DOCS,
         'description': 'Title, subtitle, citation and per-column metadata (titles, units, sources, licences) of one chart.',
         'parameters': [p('slug', 'Chart name from its URL, e.g. life-expectancy.', location='path', required=True),
                        p('v', 'API version; 1.', default='1')],
         'response': obj({
             'chart': obj({'title': leaf('string', 'Chart title.'), 'subtitle': leaf('string', 'Chart subtitle.'),
                           'citation': leaf('string', 'How to cite the chart.'),
                           'originalChartUrl': leaf('string', 'Link to the chart.')}),
             'columns': leaf('object', 'Metadata of each indicator column, keyed by column name: titleShort, unit, '
                                       'shortUnit, descriptionShort, timespan, citationShort, owidVariableId and more.',
                             additionalProperties=True),
             'dateDownloaded': leaf('string', 'Date the metadata was produced.'),
         })},
    ],
}


# --- Eurostat ----------------------------------------------------------------------------------
EUROSTAT_SDMX_DOCS = 'https://wikis.ec.europa.eu/display/EUROSTATHELP/API+SDMX+2.1+-+data+query'
EUROSTAT_STATS_DOCS = 'https://wikis.ec.europa.eu/display/EUROSTATHELP/API+Statistics+-+data+query'
EUROSTAT = {
    'definition': {
        'source_name': 'Eurostat Data Source', 'provider': 'Eurostat (European Commission)',
        'description': 'European statistics by country and region: economy, prices, labour, population, '
                       'government finance and more, through the SDMX 2.1 and JSON-stat dissemination APIs.',
        'coverage': ['EU and EFTA countries', 'candidate countries', 'European regions', 'macroeconomics'],
        'authentication': 'No key is required; reuse is free with acknowledgement of the source.',
        'license': 'Free reuse with acknowledgement (Commission Decision 2011/833/EU); data for countries outside the EU, '
                   'EFTA and candidate countries are for non-commercial use only '
                   '(https://ec.europa.eu/eurostat/help/copyright-notice).',
        'attribution': 'Source: Eurostat',
        'documentation': EUROSTAT_SDMX_DOCS,
    },
    'policy': {'base_url': 'https://ec.europa.eu/eurostat/api/dissemination', 'required': False,
               'allowed_base_hosts': ('ec.europa.eu',)},
    'endpoints': [
        {'identity': 'sdmx_data', 'name': 'Dataset observations (SDMX)', 'category': 'Statistics',
         'operation': '/sdmx/2.1/data/{flowRef}/{key}', 'documentation_url': EUROSTAT_SDMX_DOCS,
         'description': 'Observations of one dataset for a series key (dimension codes joined by dots, empty for every '
                        'value), as SDMX-CSV or SDMX-ML. In SDMX-CSV each row carries the dimension codes (geo is the '
                        'country or region code), TIME_PERIOD, OBS_VALUE and OBS_FLAG.',
         'parameters': [
             p('flowRef', 'Dataset code, e.g. une_rt_a.', location='path', required=True, examples=('une_rt_a', 'prc_hicp_aind')),
             p('key', 'Series key, dimension codes in the dataset order joined by dots, e.g. A.Y15-74.PC_ACT.T.',
               location='path', required=True),
             p('format', 'Reply format, e.g. SDMX-CSV; SDMX-ML when omitted.', examples=('SDMX-CSV',)),
             p('startPeriod', 'First period, e.g. 2020 or 2020-Q1.'),
             p('endPeriod', 'Last period.'),
             p('lastNObservations', 'Only the latest n observations of each series.', {'type': 'integer', 'minimum': 1}),
             p('firstNObservations', 'Only the first n observations of each series.', {'type': 'integer', 'minimum': 1}),
             p('detail', 'Amount of detail.', {'type': 'string', 'enum': ['full', 'dataonly', 'serieskeysonly', 'nodata']}),
         ],
         'response': TEXT_REPLY, 'media_type': 'application/vnd.sdmx.data+csv'},
        {'identity': 'statistics_data', 'name': 'Dataset observations (JSON-stat)', 'category': 'Statistics',
         'operation': '/statistics/1.0/data/{datasetCode}', 'documentation_url': EUROSTAT_STATS_DOCS,
         'description': 'Observations of one dataset as JSON-stat 2.0, filtered by any dimension given as a parameter '
                        '(geo, unit, sex ...); values are keyed by their position in the dimension lists.',
         'parameters': [
             p('datasetCode', 'Dataset code, e.g. une_rt_a.', location='path', required=True),
             p('format', 'JSON.', {'type': 'string', 'enum': ['JSON']}, default='JSON'),
             p('lang', 'Label language.', {'type': 'string', 'enum': ['EN', 'FR', 'DE']}),
             p('geo', 'Country or region code to keep, e.g. DE.'),
             p('time', 'Period to keep, e.g. 2024.'),
             p('sinceTimePeriod', 'Periods from this one on.'),
             p('untilTimePeriod', 'Periods up to this one.'),
             p('lastTimePeriod', 'Only the latest n periods.', {'type': 'integer', 'minimum': 1}),
         ],
         'response': obj({
             'version': leaf('string', 'JSON-stat version, 2.0.'),
             'class': leaf('string', 'dataset.'),
             'label': leaf('string', 'Dataset title.'),
             'source': leaf('string', 'ESTAT.'),
             'updated': leaf('string', 'Time the dataset was last updated.'),
             'id': arr(STRING, 'Dimension names in order.'),
             'size': arr(INTEGER, 'Number of categories of each dimension, in order.'),
             'value': leaf('object', 'Observation values keyed by their flat position across the dimensions.',
                           additionalProperties=True),
             'status': leaf('object', 'Observation flags keyed like value.', additionalProperties=True),
             'dimension': leaf('object', 'Each dimension\'s label and categories (index and label).', additionalProperties=True),
         })},
    ],
}


# --- WHO Global Health Observatory -------------------------------------------------------------
WHO_DOCS = 'https://www.who.int/data/gho/info/gho-odata-api'
WHO_FACT = obj({
    'Id': leaf('integer', 'Identifier of the fact.'),
    'IndicatorCode': leaf('string', 'GHO indicator code, e.g. WHOSIS_000001.'),
    'SpatialDimType': leaf('string', 'Kind of place: COUNTRY, REGION, WORLDBANKINCOMEGROUP, GLOBAL ...'),
    'SpatialDim': leaf('string', 'Place code: ISO 3166-1 alpha-3 for a country, otherwise a WHO code.'),
    'ParentLocationCode': leaf(['string', 'null'], 'Code of the WHO region of the place.'),
    'ParentLocation': leaf(['string', 'null'], 'Name of the WHO region of the place.'),
    'TimeDimType': leaf('string', 'Kind of period, usually YEAR.'),
    'TimeDim': leaf('integer', 'Period, usually a year.'),
    'Dim1Type': leaf(['string', 'null'], 'First breakdown, e.g. SEX.'),
    'Dim1': leaf(['string', 'null'], 'First breakdown value, e.g. SEX_BTSX for both sexes.'),
    'Dim2Type': leaf(['string', 'null'], 'Second breakdown, e.g. AGEGROUP.'),
    'Dim2': leaf(['string', 'null'], 'Second breakdown value.'),
    'Dim3Type': leaf(['string', 'null'], 'Third breakdown.'),
    'Dim3': leaf(['string', 'null'], 'Third breakdown value.'),
    'Value': leaf(['string', 'null'], 'Display value, with its uncertainty interval when there is one.'),
    'NumericValue': leaf(['number', 'null'], 'Numeric value of the fact.'),
    'Low': leaf(['number', 'null'], 'Lower bound of the uncertainty interval.'),
    'High': leaf(['number', 'null'], 'Upper bound of the uncertainty interval.'),
    'Comments': leaf(['string', 'null'], 'Notes on the value.'),
    'Date': leaf('string', 'Time the fact was last updated.'),
    'TimeDimensionBegin': leaf('string', 'Start of the period.'),
    'TimeDimensionEnd': leaf('string', 'End of the period.'),
})
ODATA = [p('$filter', "OData filter, e.g. SpatialDimType eq 'COUNTRY' and TimeDim ge 2015."),
         p('$select', 'Comma-separated fields to return.'),
         p('$orderby', 'Sort, e.g. TimeDim desc.'),
         p('$top', 'Most rows to return.', {'type': 'integer', 'minimum': 1}),
         p('$skip', 'Rows to skip.', {'type': 'integer', 'minimum': 0})]
WHO = {
    'definition': {
        'source_name': 'WHO Health Data Source', 'provider': 'World Health Organization',
        'description': 'WHO Global Health Observatory indicators by country and year: life expectancy, '
                       'mortality, disease, health systems, spending and risk factors, through the GHO OData API.',
        'coverage': ['global health indicators', 'countries', 'WHO regions'],
        'authentication': 'No key is required.',
        'license': 'WHO terms for data compilations: public-health purposes only, not to promote a commercial enterprise, '
                   'no sale or transfer (https://www.who.int/about/policies/publishing/data-policy/terms-and-conditions).',
        'attribution': 'WHO, Global Health Observatory',
        'documentation': WHO_DOCS,
    },
    'policy': {'base_url': 'https://ghoapi.azureedge.net', 'required': False, 'allowed_base_hosts': ('ghoapi.azureedge.net',)},
    'endpoints': [
        {'identity': 'indicator_data', 'name': 'Indicator values', 'category': 'Health indicators',
         'operation': '/api/{indicatorCode}', 'documentation_url': WHO_DOCS,
         'description': 'Every value of one GHO indicator by place, period and breakdown, narrowed with an OData filter.',
         'parameters': [p('indicatorCode', 'GHO indicator code, e.g. WHOSIS_000001 (life expectancy at birth).',
                          location='path', required=True, examples=('WHOSIS_000001', 'UHC_INDEX_REPORTED')), *ODATA],
         'response': obj({'@odata.context': leaf('string', 'OData metadata link.'), 'value': arr(WHO_FACT, 'The values.')})},
        {'identity': 'indicators', 'name': 'Indicator list', 'category': 'Health indicators',
         'operation': '/api/Indicator', 'documentation_url': WHO_DOCS,
         'description': 'Every GHO indicator code with its name.',
         'parameters': ODATA,
         'response': obj({'value': arr(obj({'IndicatorCode': leaf('string', 'Indicator code.'),
                                            'IndicatorName': leaf('string', 'Indicator name.'),
                                            'Language': leaf('string', 'Language of the name.')}), 'The indicators.')})},
        {'identity': 'dimension_values', 'name': 'Dimension values', 'category': 'Health indicators',
         'operation': '/api/DIMENSION/{dimensionCode}/DimensionValues', 'documentation_url': WHO_DOCS,
         'description': 'The codes and names of one dimension, e.g. COUNTRY, SEX or AGEGROUP.',
         'parameters': [p('dimensionCode', 'Dimension, e.g. COUNTRY.', location='path', required=True), *ODATA],
         'response': obj({'value': arr(obj({'Code': leaf('string', 'Value code.'), 'Title': leaf('string', 'Value name.'),
                                            'ParentDimension': leaf(['string', 'null'], 'Parent dimension.'),
                                            'Dimension': leaf('string', 'Dimension.'),
                                            'ParentCode': leaf(['string', 'null'], 'Parent value code.'),
                                            'ParentTitle': leaf(['string', 'null'], 'Parent value name.')}), 'The values.')})},
    ],
}


# --- Taiwan Central Weather Administration -----------------------------------------------------
CWA_DOCS = 'https://opendata.cwa.gov.tw/dist/opendata-swagger.html'
CWA_REPLY = {'success': leaf('string', '"true" when the request succeeded.'),
             'result': obj({'resource_id': leaf('string', 'Dataset code.'),
                            'fields': arr(obj({'id': leaf('string', 'Field name.'), 'type': leaf('string', 'Field type.')}),
                                          'Fields of the dataset.')})}
CWA_EARTHQUAKE = obj({
    'EarthquakeNo': leaf('integer', 'Report number.'),
    'ReportType': leaf('string', 'Report type.'),
    'ReportColor': leaf('string', 'Colour grade of the report (綠色, 黃色, 橙色, 紅色).'),
    'ReportContent': leaf('string', 'Summary text of the report.'),
    'ReportImageURI': leaf('string', 'Link to the report image.'),
    'ShakemapImageURI': leaf('string', 'Link to the shake map image.'),
    'ReportRemark': leaf('string', 'Remarks.'),
    'Web': leaf('string', 'Link to the report page.'),
    'EarthquakeInfo': obj({
        'OriginTime': leaf('string', 'Origin time in Taiwan time (UTC+8), "YYYY-MM-DD hh:mm:ss".'),
        'Source': leaf('string', 'Reporting agency.'),
        'FocalDepth': leaf('number', 'Focal depth in kilometres.'),
        'Epicenter': obj({'Location': leaf('string', 'Description of the epicentre location.'),
                          'EpicenterLatitude': leaf('number', 'Epicentre latitude, WGS84 decimal degrees.'),
                          'EpicenterLongitude': leaf('number', 'Epicentre longitude, WGS84 decimal degrees.')}),
        'EarthquakeMagnitude': obj({'MagnitudeType': leaf('string', 'Magnitude scale (芮氏規模, local magnitude ML).'),
                                    'MagnitudeValue': leaf('number', 'Magnitude.')}),
    }),
    'Intensity': obj({'ShakingArea': arr(leaf('object', 'Seismic intensity by county and station.', additionalProperties=True),
                                         'Areas that felt the earthquake.')}),
}, 'One earthquake report.')
CWA_STATION = obj({
    'StationName': leaf('string', 'Station name.'),
    'StationId': leaf('string', 'Station identifier.'),
    'ObsTime': obj({'DateTime': leaf('string', 'Observation time, ISO 8601 with offset +08:00.')}),
    'GeoInfo': obj({
        'Coordinates': arr(obj({'CoordinateName': leaf('string', 'Datum: TWD67 or WGS84.'),
                                'CoordinateFormat': leaf('string', 'decimal degrees.'),
                                'StationLatitude': leaf('number', 'Station latitude in that datum.'),
                                'StationLongitude': leaf('number', 'Station longitude in that datum.')}),
                           'Station position, once per datum.'),
        'StationAltitude': leaf('string', 'Station altitude in metres.'),
        'CountyName': leaf('string', 'County.'), 'TownName': leaf('string', 'Township.'),
        'CountyCode': leaf('string', 'County code.'), 'TownCode': leaf('string', 'Township code.'),
    }),
    'WeatherElement': obj({
        'Weather': leaf('string', 'Weather description.'),
        'Now': obj({'Precipitation': leaf('number', 'Precipitation today so far, mm.')}),
        'WindDirection': leaf('number', 'Wind direction, degrees.'),
        'WindSpeed': leaf('number', 'Wind speed, m/s.'),
        'AirTemperature': leaf('number', 'Air temperature, °C; -99 when not measured.'),
        'RelativeHumidity': leaf('number', 'Relative humidity, %; -99 when not measured.'),
        'AirPressure': leaf('number', 'Air pressure, hPa; -99 when not measured.'),
        'GustInfo': leaf('object', 'Peak gust speed, direction and time.', additionalProperties=True),
        'DailyExtreme': leaf('object', 'Daily high and low temperatures with their times.', additionalProperties=True),
    }),
}, 'One weather station observation.')
CWA_COMMON = [p('limit', 'Most records to return.', {'type': 'integer', 'minimum': 1}),
              p('offset', 'Records to skip.', {'type': 'integer', 'minimum': 0}),
              p('format', 'Reply format.', {'type': 'string', 'enum': ['JSON', 'XML']}, default='JSON')]
CWA_QUAKE_FILTERS = [p('AreaName', 'Counties to keep.', {'type': 'array', 'items': STRING}),
                     p('StationName', 'Stations to keep.', {'type': 'array', 'items': STRING}),
                     p('sort', 'Sort field.', {'type': 'string', 'enum': ['OriginTime']}),
                     p('timeFrom', 'Earliest origin time, yyyy-MM-ddThh:mm:ss.'),
                     p('timeTo', 'Latest origin time, yyyy-MM-ddThh:mm:ss.')]
CWA_STATION_FILTERS = [p('StationId', 'Stations to keep, by identifier.', {'type': 'array', 'items': STRING}),
                       p('StationName', 'Stations to keep, by name.', {'type': 'array', 'items': STRING}),
                       p('WeatherElement', 'Weather elements to return.', {'type': 'array', 'items': STRING}),
                       p('GeoInfo', 'Location elements to return.', {'type': 'array', 'items': STRING})]


def _cwa_quakes(code, name, description):
    return {'identity': code.lower().replace('-', '_'), 'name': name, 'category': 'Earthquake reports',
            'operation': f'/v1/rest/datastore/{code}', 'documentation_url': CWA_DOCS, 'description': description,
            'parameters': [*CWA_COMMON, *CWA_QUAKE_FILTERS],
            'response': obj({**CWA_REPLY, 'records': obj({'datasetDescription': leaf('string', 'Dataset name.'),
                                                          'Earthquake': arr(CWA_EARTHQUAKE, 'Earthquake reports, newest first.')})})}


def _cwa_stations(code, name, description):
    return {'identity': code.lower().replace('-', '_'), 'name': name, 'category': 'Weather observations',
            'operation': f'/v1/rest/datastore/{code}', 'documentation_url': CWA_DOCS, 'description': description,
            'parameters': [*CWA_COMMON, *CWA_STATION_FILTERS],
            'response': obj({**CWA_REPLY, 'records': obj({'Station': arr(CWA_STATION, 'Station observations.')})})}


CWA = {
    'definition': {
        'source_name': 'Taiwan Weather (CWA) Data Source', 'provider': 'Central Weather Administration (Taiwan)',
        'description': 'Taiwan Central Weather Administration open data: earthquake reports with epicentres and '
                       'magnitudes, and observations from its automatic and manned weather stations.',
        'coverage': ['Taiwan', 'earthquakes', 'weather observations'],
        'authentication': 'A free personal authorization code from the CWA open data platform; it stays in your Terminal.',
        'authentication_required': True,
        'license': 'Open Government Data License, Taiwan v1.0 (https://data.gov.tw/license): any use, with this notice '
                   'naming the agency and dataset; without it the licence is not granted.',
        'attribution': '交通部中央氣象署 Central Weather Administration open data, under the Open Government Data License, Taiwan v1.0 (data.gov.tw/license)',
        'documentation': CWA_DOCS,
    },
    'policy': {'base_url': 'https://opendata.cwa.gov.tw/api', 'auth': ('query', 'Authorization', ''),
               'allowed_base_hosts': ('opendata.cwa.gov.tw',)},
    'key_slot': True,
    'endpoints': [
        _cwa_quakes('E-A0015-001', 'Significant earthquake reports (顯著有感地震報告)',
                    'Numbered reports of significant felt earthquakes in and around Taiwan: origin time, epicentre, '
                    'depth, magnitude and intensities.'),
        _cwa_quakes('E-A0016-001', 'Small-area earthquake reports (小區域有感地震報告)',
                    'Reports of small felt earthquakes in Taiwan: origin time, epicentre, depth, magnitude and intensities.'),
        _cwa_stations('O-A0001-001', 'Automatic weather station observations (自動氣象站)',
                      'Latest hourly observation of every automatic weather station: temperature, humidity, pressure, '
                      'wind and rain, with WGS84 and TWD67 positions.'),
        _cwa_stations('O-A0003-001', 'Manned weather station observations (綜觀氣象站)',
                      'Latest 10-minute observation of every manned (synoptic) station, including weather, visibility '
                      'and UV index.'),
    ],
}


# --- Taiwan Ministry of Environment ------------------------------------------------------------
MOENV_DOCS = 'https://data.moenv.gov.tw/swagger/'
MOENV_AQI = obj({
    'sitename': leaf('string', 'Monitoring station name.'),
    'siteid': leaf('string', 'Monitoring station identifier.'),
    'county': leaf('string', 'County.'),
    'aqi': leaf('string', 'Air quality index (numeric text); empty when not reported.'),
    'pollutant': leaf('string', 'Main pollutant.'),
    'status': leaf('string', 'Air quality status, e.g. 良好 (good).'),
    'so2': leaf('string', 'Sulphur dioxide, ppb.'), 'co': leaf('string', 'Carbon monoxide, ppm.'),
    'o3': leaf('string', 'Ozone, ppb.'), 'o3_8hr': leaf('string', 'Ozone 8-hour average, ppb.'),
    'pm10': leaf('string', 'PM10, μg/m3.'), 'pm2.5': leaf('string', 'PM2.5, μg/m3.'),
    'no2': leaf('string', 'Nitrogen dioxide, ppb.'), 'nox': leaf('string', 'Nitrogen oxides, ppb.'),
    'no': leaf('string', 'Nitric oxide, ppb.'),
    'wind_speed': leaf('string', 'Wind speed, m/s.'), 'wind_direc': leaf('string', 'Wind direction, degrees.'),
    'publishtime': leaf('string', 'Publication time in Taiwan time, "YYYY/MM/DD hh:mm:ss".'),
    'co_8hr': leaf('string', 'Carbon monoxide 8-hour average, ppm.'),
    'pm2.5_avg': leaf('string', 'PM2.5 moving average, μg/m3.'), 'pm10_avg': leaf('string', 'PM10 moving average, μg/m3.'),
    'so2_avg': leaf('string', 'Sulphur dioxide 24-hour average, ppb.'),
    'longitude': leaf('string', 'Station longitude, WGS84 decimal degrees.'),
    'latitude': leaf('string', 'Station latitude, WGS84 decimal degrees.'),
}, 'One monitoring station\'s hourly reading.')
MOENV = {
    'definition': {
        'source_name': 'Taiwan Environment (MOENV) Data Source', 'provider': 'Ministry of Environment (Taiwan)',
        'description': 'Taiwan Ministry of Environment open data: the hourly air quality index and pollutant '
                       'readings of every monitoring station.',
        'coverage': ['Taiwan', 'air quality'],
        'authentication': 'A free personal API key from the MOENV open data platform; it stays in your Terminal.',
        'authentication_required': True,
        'license': 'Open Government Data License, Taiwan v1.0 (https://data.gov.tw/license): any use, with this notice '
                   'naming the agency and dataset; without it the licence is not granted.',
        'attribution': '環境部 Ministry of Environment open data, under the Open Government Data License, Taiwan v1.0 (data.gov.tw/license)',
        'documentation': MOENV_DOCS,
    },
    'policy': {'base_url': 'https://data.moenv.gov.tw/api/v2', 'auth': ('query', 'api_key', ''),
               'allowed_base_hosts': ('data.moenv.gov.tw',)},
    'key_slot': True,
    'endpoints': [
        {'identity': 'aqx_p_432', 'name': 'Air quality index, hourly (空氣品質指標 AQI)', 'category': 'Air quality',
         'operation': '/aqx_p_432', 'documentation_url': MOENV_DOCS,
         'description': 'The latest hourly air quality index of every monitoring station with its main pollutant, '
                        'pollutant concentrations, wind and position.',
         'parameters': [p('format', 'Reply format.', {'type': 'string', 'enum': ['JSON', 'XML', 'CSV']}, default='JSON'),
                        p('language', 'zh or en.', {'type': 'string', 'enum': ['zh', 'en']}, default='zh'),
                        p('offset', 'Records to skip.', {'type': 'integer', 'minimum': 0}, default=0),
                        p('limit', 'Most records to return.', {'type': 'integer', 'minimum': 1}, default=1000)],
         'response': obj({'total': leaf('string', 'Number of records.'),
                          'records': arr(MOENV_AQI, 'Station readings.'),
                          'fields': arr(obj({'id': leaf('string', 'Field name.'),
                                             'info': leaf('object', 'Field label.', additionalProperties=True)}),
                                        'Fields of the dataset.')})},
    ],
}


# --- Map tiles ------------------------------------------------------------------------------------------------------
# A tile is one 256 x 256 picture of the Web Mercator grid (EPSG:3857): zoom level, row from the north, column from
# the antimeridian. The Data Source passes it on as base64 with its media type.
IMAGE_REPLY = obj({'content_base64': leaf('string', 'The picture, base64.'),
                   'content_type': leaf('string', 'Media type of the picture: image/jpeg or image/png.')},
                  'A picture reply, as the Data Source passes it on.', ('content_base64', 'content_type'))
TILE_ADDRESS = [
    p('TileMatrix', 'Zoom level of the tile, 0 for the whole world.', {'type': 'integer', 'minimum': 0, 'maximum': 22},
      location='path', required=True),
    p('TileRow', 'Row of the tile, counted from the north.', {'type': 'integer', 'minimum': 0}, location='path', required=True),
    p('TileCol', 'Column of the tile, counted east from the antimeridian.', {'type': 'integer', 'minimum': 0},
      location='path', required=True),
]

# --- NASA Global Imagery Browse Services (GIBS) --------------------------------------------------------------------
GIBS_DOCS = 'https://nasa-gibs.github.io/gibs-api-docs/access-basics/'
# The layers this source offers, each with the deepest Web Mercator tile set GIBS publishes for it and its format.
GIBS_LAYERS = {
    'ASTER_GDEM_Color_Shaded_Relief': ('GoogleMapsCompatible_Level12', 'jpg'),
    'ASTER_GDEM_Greyscale_Shaded_Relief': ('GoogleMapsCompatible_Level12', 'jpg'),
    'BlueMarble_ShadedRelief_Bathymetry': ('GoogleMapsCompatible_Level8', 'jpg'),
    'VIIRS_Black_Marble': ('GoogleMapsCompatible_Level8', 'png'),
}
GIBS = {
    'definition': {
        'source_name': 'NASA GIBS Imagery Data Source', 'provider': 'NASA Global Imagery Browse Services (GIBS)',
        'description': 'Map tiles of the whole Earth from NASA: terrain as colour or grey shaded relief from the '
                       'ASTER global elevation model (about 30 m), Blue Marble land and sea floor, and the Black '
                       'Marble night lights.',
        'coverage': ['global', 'terrain', 'imagery', 'map tiles'],
        'authentication': 'No key is required.',
        'license': 'NASA imagery has no restrictions on use; credit NASA EOSDIS GIBS. ASTER GDEM is a product of METI and NASA.',
        'attribution': 'NASA EOSDIS GIBS · ASTER GDEM: METI and NASA',
        'documentation': GIBS_DOCS,
    },
    'policy': {'base_url': 'https://gibs.earthdata.nasa.gov', 'required': False,
               'headers': {'Accept': 'image/jpeg, image/png;q=0.9'}, 'allowed_base_hosts': ('gibs.earthdata.nasa.gov',)},
    'endpoints': [
        {'identity': 'wmts_tile', 'name': 'Web Mercator map tile', 'category': 'Map tiles',
         'operation': '/wmts/epsg3857/best/{Layer}/default/{Time}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.{format}',
         'documentation_url': GIBS_DOCS, 'media_type': 'image/jpeg',
         'description': 'One 256-pixel tile of a layer in Web Mercator (WMTS REST). A layer has tiles down to the '
                        'deepest level of its tile set; deeper ones are refused.',
         'parameters': [
             p('Layer', 'GIBS layer identifier.', {'type': 'string', 'enum': list(GIBS_LAYERS)}, location='path', required=True),
             p('Time', 'Date of the imagery, or default for the layer\'s latest.', location='path', required=True,
               default='default', examples=('default', '2024-06-01')),
             p('TileMatrixSet', 'The layer\'s tile set: GoogleMapsCompatible_Level<deepest zoom>.',
               {'type': 'string', 'enum': sorted({value[0] for value in GIBS_LAYERS.values()})}, location='path', required=True),
             *TILE_ADDRESS,
             p('format', 'File type of the layer\'s tiles.', {'type': 'string', 'enum': ['jpg', 'png']}, location='path', required=True),
         ],
         'response': IMAGE_REPLY},
    ],
}

# --- EOX Sentinel-2 cloudless ---------------------------------------------------------------------------------------
EOX_DOCS = 'https://s2maps.eu'
EOX = {
    'definition': {
        'source_name': 'EOX Sentinel-2 Cloudless Data Source', 'provider': 'EOX IT Services',
        'description': 'Cloud-free satellite map of the whole Earth from Copernicus Sentinel-2 at 10 m, as map tiles: '
                       'the 2016 and 2017 mosaics, which are free for any use with credit.',
        'coverage': ['global', 'imagery', 'map tiles'],
        'authentication': 'No key is required.',
        'license': 'Creative Commons Attribution 4.0 (CC BY 4.0) for the 2016 and 2017 mosaics.',
        'attribution': 'EOxCloudless https://cloudless.eox.at by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2016 & 2017)',
        'documentation': EOX_DOCS,
    },
    'policy': {'base_url': 'https://tiles.maps.eox.at', 'required': False, 'headers': {'Accept': 'image/jpeg'},
               'allowed_base_hosts': ('tiles.maps.eox.at',)},
    'endpoints': [
        {'identity': 'wmts_tile', 'name': 'Web Mercator map tile', 'category': 'Map tiles',
         'operation': '/wmts/1.0.0/{Layer}/default/g/{TileMatrix}/{TileRow}/{TileCol}.jpg',
         'documentation_url': 'https://tiles.maps.eox.at/wmts/1.0.0/WMTSCapabilities.xml', 'media_type': 'image/jpeg',
         'description': 'One 256-pixel JPEG tile of a yearly cloudless mosaic in Web Mercator (WMTS REST).',
         'parameters': [
             p('Layer', 'The mosaic: s2cloudless_3857 (2016) or s2cloudless-2017_3857 (2017).',
               {'type': 'string', 'enum': ['s2cloudless_3857', 's2cloudless-2017_3857']}, location='path', required=True),
             *TILE_ADDRESS,
         ],
         'response': IMAGE_REPLY},
    ],
}


# --- OpenStreetMap ----------------------------------------------------------------------------------------------------
# OpenStreetMap's tile servers and OpenTopoMap ask every application to identify itself in its User-Agent and to credit
# OpenStreetMap; their capacity is donated, so heavy use belongs on a provider's own servers. The policy blocks generic
# library User-Agents: this one names the application and where to find it, and keeps its product name and contact.
TILE_CLIENT = 'RetisTerminal/1.0 (+https://retis.ai)'
XYZ_TILE = [
    p('TileMatrix', 'Zoom level of the tile, 0 for the whole world.', {'type': 'integer', 'minimum': 0, 'maximum': 22},
      location='path', required=True),
    p('TileCol', 'Column of the tile, counted east from the antimeridian.', {'type': 'integer', 'minimum': 0}, location='path', required=True),
    p('TileRow', 'Row of the tile, counted from the north.', {'type': 'integer', 'minimum': 0}, location='path', required=True),
]
OSM_DOCS = 'https://operations.osmfoundation.org/policies/tiles/'
OSM = {
    'definition': {
        'source_name': 'OpenStreetMap Tiles Data Source', 'provider': 'OpenStreetMap Foundation and OpenTopoMap',
        'description': 'OpenStreetMap as map tiles: the standard street map from the OpenStreetMap Foundation\'s tile servers '
                       '(to zoom 19) and the topographic map with contours and hill shading from OpenTopoMap (to zoom 17).',
        'coverage': ['global', 'streets', 'terrain', 'map tiles'],
        'authentication': 'No key is required; the tile servers ask for light use and an identifying User-Agent.',
        'license': 'Map data © OpenStreetMap contributors (ODbL); standard tiles CC BY-SA 2.0; OpenTopoMap tiles CC BY-SA 3.0.',
        # OpenTopoMap credits its map as "© OpenStreetMap contributors, SRTM | © OpenTopoMap (CC-BY-SA)".
        'attribution': '© OpenStreetMap contributors · topographic map: © OpenTopoMap (CC-BY-SA), SRTM',
        'documentation': OSM_DOCS,
    },
    'policy': {'base_url': 'https://tile.openstreetmap.org', 'required': False,
               'headers': {'Accept': 'image/png', 'User-Agent': TILE_CLIENT},
               'allowed_base_hosts': ('tile.openstreetmap.org', 'tile.opentopomap.org')},
    'endpoints': [
        {'identity': 'standard_tile', 'name': 'Standard map tile', 'category': 'Map tiles',
         'operation': '/{TileMatrix}/{TileCol}/{TileRow}.png', 'documentation_url': OSM_DOCS, 'media_type': 'image/png',
         'description': 'One 256-pixel PNG tile of the standard OpenStreetMap map, zoom 0 to 19.',
         'parameters': XYZ_TILE, 'response': IMAGE_REPLY},
        {'identity': 'topographic_tile', 'name': 'OpenTopoMap tile', 'category': 'Map tiles', 'base_url': 'https://tile.opentopomap.org',
         'operation': '/{TileMatrix}/{TileCol}/{TileRow}.png', 'documentation_url': 'https://opentopomap.org/about', 'media_type': 'image/png',
         'description': 'One 256-pixel PNG tile of OpenTopoMap: OpenStreetMap data with contours and hill shading, zoom 0 to 17.',
         'parameters': XYZ_TILE, 'response': IMAGE_REPLY},
    ],
}

# --- VersaTiles: OpenStreetMap as vector tiles ------------------------------------------------------------------------
VERSATILES_DOCS = 'https://docs.versatiles.org/'
VECTOR_REPLY = obj({'content_base64': leaf('string', 'The vector tile (Mapbox Vector Tile, protobuf), base64.'),
                    'content_type': leaf('string', 'Media type of the tile: application/vnd.mapbox-vector-tile.')},
                   'A vector tile, as the Data Source passes it on.', ('content_base64', 'content_type'))
VERSATILES = {
    'definition': {
        'source_name': 'VersaTiles OpenStreetMap Data Source', 'provider': 'VersaTiles',
        'description': 'OpenStreetMap features as vector tiles in the Shortbread scheme: streets and railways, buildings, '
                       'water, land use, places and points of interest, down to zoom 14 (about 2 km a tile).',
        'coverage': ['global', 'streets', 'buildings', 'points of interest', 'vector tiles'],
        'authentication': 'No key is required.',
        'license': 'Map data © OpenStreetMap contributors (ODbL).',
        'attribution': '© OpenStreetMap contributors · VersaTiles',
        'documentation': VERSATILES_DOCS,
    },
    'policy': {'base_url': 'https://tiles.versatiles.org', 'required': False,
               'headers': {'Accept': 'application/vnd.mapbox-vector-tile', 'User-Agent': TILE_CLIENT},
               'allowed_base_hosts': ('tiles.versatiles.org',)},
    'endpoints': [
        {'identity': 'osm_tile', 'name': 'OpenStreetMap vector tile', 'category': 'Vector tiles',
         'operation': '/tiles/osm/{TileMatrix}/{TileCol}/{TileRow}', 'documentation_url': VERSATILES_DOCS,
         'media_type': 'application/vnd.mapbox-vector-tile',
         'description': 'One Mapbox Vector Tile of OpenStreetMap in the Shortbread scheme, zoom 0 to 14; its layers include '
                        'streets, buildings, water_polygons, water_lines, land, pois and place_labels.',
         'parameters': [{**row, 'schema': {**row['schema'], 'maximum': 14} if row['name'] == 'TileMatrix' else row['schema']}
                        for row in XYZ_TILE]
                       + [p('layers', 'Only these layers of the tile, comma-separated (such as streets or buildings). The Data '
                                      'Source leaves the other layers out of its reply and never sends this to VersaTiles.')],
         'response': VECTOR_REPLY},
    ],
}

# --- Street map tiles read with each user's own key ------------------------------------------------------------------
# The key stays in the user's Terminal, which asks the vendor itself; these sources only publish where the key goes.
STADIA_DOCS = 'https://docs.stadiamaps.com/raster/'
STADIA = {
    'definition': {
        'source_name': 'Stadia Maps Data Source', 'provider': 'Stadia Maps',
        'description': 'Street and terrain map tiles from Stadia Maps: Alidade Smooth (light and dark), OSM Bright, Outdoors, '
                       'and the Stamen Terrain and Toner styles, read with your own Stadia Maps key.',
        'coverage': ['global', 'streets', 'terrain', 'map tiles'],
        'authentication': 'A Stadia Maps API key of your own from a paid plan (Starter or above); it stays in your Terminal. '
                          'The free plan is for non-commercial use or evaluation only, and Stadia counts any use by a '
                          'for-profit company, Retis included, as commercial.',
        'authentication_required': True,
        'license': 'Stadia Maps terms (https://stadiamaps.com/terms-of-service/): use in Retis needs a paid plan; tiles are cached '
                   'on your device only, never on a server. © Stadia Maps, © OpenMapTiles, © OpenStreetMap contributors '
                   '(Stamen styles © Stamen Design).',
        'attribution': '© Stadia Maps © OpenMapTiles © OpenStreetMap contributors (Stamen styles © Stamen Design)',
        'documentation': STADIA_DOCS,
    },
    'policy': {'base_url': 'https://tiles.stadiamaps.com', 'auth': ('query', 'api_key', ''),
               'headers': {'Accept': 'image/png'}, 'allowed_base_hosts': ('tiles.stadiamaps.com',)},
    'key_slot': True,
    'endpoints': [
        {'identity': 'raster_tile', 'name': 'Raster map tile', 'category': 'Map tiles',
         'operation': '/tiles/{style}/{TileMatrix}/{TileCol}/{TileRow}.png', 'documentation_url': STADIA_DOCS, 'media_type': 'image/png',
         'description': 'One 256-pixel PNG tile of a Stadia Maps style.',
         'parameters': [p('style', 'Map style.', {'type': 'string', 'enum': ['alidade_smooth', 'alidade_smooth_dark', 'osm_bright',
                                                                             'outdoors', 'stamen_terrain', 'stamen_toner']},
                          location='path', required=True), *XYZ_TILE],
         'response': IMAGE_REPLY},
    ],
}
MAPTILER_DOCS = 'https://docs.maptiler.com/cloud/api/maps/'
MAPTILER = {
    'definition': {
        'source_name': 'MapTiler Data Source', 'provider': 'MapTiler',
        'description': 'Map tiles from MapTiler Cloud: Streets, Outdoor, Topo, Basic and Satellite, read with your own MapTiler key.',
        'coverage': ['global', 'streets', 'terrain', 'imagery', 'map tiles'],
        'authentication': 'A MapTiler API key of your own from a paid plan (Flex or above); it stays in your Terminal. '
                          'The free plan is for non-commercial use and also requires the MapTiler logo on the map.',
        'authentication_required': True,
        'license': 'MapTiler Cloud terms (https://www.maptiler.com/terms/cloud/): use in Retis needs a paid plan; tiles are cached on '
                   'your device only, never on a server. © MapTiler, © OpenStreetMap contributors.',
        'attribution': '© MapTiler © OpenStreetMap contributors',
        'documentation': MAPTILER_DOCS,
    },
    'policy': {'base_url': 'https://api.maptiler.com', 'auth': ('query', 'key', ''),
               'headers': {'Accept': 'image/png, image/jpeg;q=0.9'}, 'allowed_base_hosts': ('api.maptiler.com',)},
    'key_slot': True,
    'endpoints': [
        {'identity': 'raster_tile', 'name': 'Raster map tile', 'category': 'Map tiles',
         'operation': '/maps/{map}/256/{TileMatrix}/{TileCol}/{TileRow}.{format}', 'documentation_url': MAPTILER_DOCS,
         'media_type': 'image/png',
         'description': 'One 256-pixel tile of a MapTiler map: PNG for drawn maps, JPEG for satellite.',
         'parameters': [p('map', 'Map identifier.', {'type': 'string', 'enum': ['streets-v2', 'outdoor-v2', 'topo-v2', 'basic-v2', 'satellite']},
                          location='path', required=True), *XYZ_TILE,
                        p('format', 'File type: png, or jpg for satellite.', {'type': 'string', 'enum': ['png', 'jpg']},
                          location='path', required=True)],
         'response': IMAGE_REPLY},
    ],
}
THUNDERFOREST_DOCS = 'https://www.thunderforest.com/docs/map-tiles-api/'
THUNDERFOREST = {
    'definition': {
        'source_name': 'Thunderforest Data Source', 'provider': 'Thunderforest',
        'description': 'Map tiles from Thunderforest: OpenCycleMap, Transport, Landscape, Outdoors, Atlas and Neighbourhood, '
                       'read with your own Thunderforest key.',
        'coverage': ['global', 'streets', 'terrain', 'map tiles'],
        'authentication': 'A Thunderforest API key of your own; it stays in your Terminal.',
        'authentication_required': True,
        'license': 'Thunderforest terms; maps © Thunderforest, data © OpenStreetMap contributors.',
        'attribution': 'Maps © Thunderforest, Data © OpenStreetMap contributors',
        'documentation': THUNDERFOREST_DOCS,
    },
    'policy': {'base_url': 'https://tile.thunderforest.com', 'auth': ('query', 'apikey', ''),
               'headers': {'Accept': 'image/png'}, 'allowed_base_hosts': ('tile.thunderforest.com',)},
    'key_slot': True,
    'endpoints': [
        {'identity': 'raster_tile', 'name': 'Raster map tile', 'category': 'Map tiles',
         'operation': '/{style}/{TileMatrix}/{TileCol}/{TileRow}.png', 'documentation_url': THUNDERFOREST_DOCS, 'media_type': 'image/png',
         'description': 'One 256-pixel PNG tile of a Thunderforest style.',
         'parameters': [p('style', 'Map style.', {'type': 'string', 'enum': ['cycle', 'transport', 'landscape', 'outdoors', 'atlas',
                                                                             'neighbourhood']},
                          location='path', required=True), *XYZ_TILE],
         'response': IMAGE_REPLY},
    ],
}


# --- Google Maps Platform: Map Tiles API (2D tiles), read with each user's own key -------------------------------------
# A map's tiles are read in a session: createSession (POST, JSON body) fixes the map type, language and region and
# answers a token, valid about two weeks, that every tile request carries. The key goes in the X-Goog-Api-Key header
# (Google's APIs read it there as well as from `key`), so it never sits in a URL.
GOOGLE_TILES_DOCS = 'https://developers.google.com/maps/documentation/tile/2d-tiles-overview'
GOOGLE_SESSION_DOCS = 'https://developers.google.com/maps/documentation/tile/session_tokens'
GOOGLE_MAPS = {
    'definition': {
        'source_name': 'Google Maps Data Source', 'provider': 'Google Maps Platform',
        'description': 'Google Maps as map tiles from the Map Tiles API: the roadmap, satellite imagery (with or without roads) '
                       'and terrain, read with your own Google Maps Platform key.',
        'coverage': ['global', 'streets', 'imagery', 'terrain', 'map tiles'],
        'authentication': 'A Google Maps Platform API key of your own with the Map Tiles API enabled; it stays in your Terminal. '
                          'Google bills your project for the tiles it reads.',
        'authentication_required': True,
        'provider_cost': 'Billed by Google to your own Google Cloud project per tile request (SKU Map Tiles API: 2D Map '
                         'Tiles), after any free monthly use; your project sets its own quota.',
        'license': 'Google Maps Platform Terms of Service and Map Tiles API policies: tiles are for showing on a map only, '
                   'never pre-fetched, stored or cached past their HTTP cache headers. Map data © Google and its data providers.',
        # Google asks for its logo, or the text "Google Maps" where space is short, and for the data credit its viewport
        # endpoint answers for the area shown.
        'attribution': 'Google Maps',
        'documentation': GOOGLE_TILES_DOCS,
    },
    'policy': {'base_url': 'https://tile.googleapis.com', 'auth': ('header', 'X-Goog-Api-Key', ''),
               'headers': {'Accept': 'image/png, image/jpeg;q=0.9'}, 'allowed_base_hosts': ('tile.googleapis.com',)},
    'key_slot': True,
    'endpoints': [
        {'identity': 'create_session', 'name': 'Create a map session', 'category': 'Map tiles', 'method': 'POST',
         'operation': '/v1/createSession', 'documentation_url': GOOGLE_SESSION_DOCS,
         'description': 'Starts a session for one kind of map: its token goes with every tile of that map, about two weeks.',
         'parameters': [
             p('mapType', 'Base map: roadmap, satellite imagery or terrain (terrain needs layerTypes ["layerRoadmap"]).',
               {'type': 'string', 'enum': ['roadmap', 'satellite', 'terrain']}, location='body', required=True),
             p('language', 'IETF language tag of the map labels, such as en-US or zh-TW.',
               {'type': 'string', 'pattern': '^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$'}, location='body', required=True,
               examples=('en-US',)),
             p('region', 'CLDR region code, two capital letters, whose borders and labels the map follows.',
               {'type': 'string', 'pattern': '^[A-Z]{2}$'}, location='body', required=True, examples=('US',)),
             p('imageFormat', 'Tile file type; Google chooses when left out.', {'type': 'string', 'enum': ['png', 'jpeg']},
               location='body'),
             p('scale', 'Size of map features: scaleFactor1x (the default), 2x or 4x.',
               {'type': 'string', 'enum': ['scaleFactor1x', 'scaleFactor2x', 'scaleFactor4x']}, location='body'),
             p('highDpi', 'Higher-resolution tiles, with scaleFactor2x or 4x.', {'type': 'boolean'}, location='body'),
             p('layerTypes', 'Layers drawn on the base map: roads and labels, Street View coverage, traffic.',
               {'type': 'array', 'maxItems': 3, 'uniqueItems': True,
                'items': {'type': 'string', 'enum': ['layerRoadmap', 'layerStreetview', 'layerTraffic']}}, location='body'),
             p('overlay', 'Only the layers, without the base map.', {'type': 'boolean'}, location='body'),
         ],
         'response': obj({'session': leaf('string', 'The session token every tile request of this map carries.'),
                          'expiry': leaf('string', 'When the token ends, in seconds since the Unix epoch.'),
                          'tileWidth': leaf('integer', 'Tile width in pixels.'),
                          'tileHeight': leaf('integer', 'Tile height in pixels.'),
                          'imageFormat': leaf('string', 'File type of the tiles: png or jpeg.')},
                         'A new map session.', ('session', 'expiry'))},
        {'identity': 'map_tile', 'name': '2D map tile', 'category': 'Map tiles',
         'operation': '/v1/2dtiles/{TileMatrix}/{TileCol}/{TileRow}', 'documentation_url': GOOGLE_TILES_DOCS,
         'media_type': 'image/png',
         'description': 'One 256-pixel tile of a session\'s map, zoom 0 to 22: PNG, or JPEG for imagery.',
         'parameters': [*XYZ_TILE,
                        p('session', 'The token createSession answered for this map.',
                          {'type': 'string', 'minLength': 1, 'maxLength': 4096}, required=True)],
         'response': IMAGE_REPLY},
        {'identity': 'viewport', 'name': 'Viewport information', 'category': 'Map tiles',
         'operation': '/tile/v1/viewport', 'documentation_url': GOOGLE_TILES_DOCS,
         'description': 'The data credit a map must show for an area at one zoom, and how deep its tiles go there.',
         'parameters': [p('session', 'The token createSession answered for this map.',
                          {'type': 'string', 'minLength': 1, 'maxLength': 4096}, required=True),
                        p('zoom', 'Zoom level of the area shown.', {'type': 'integer', 'minimum': 0, 'maximum': 22}, required=True),
                        *(p(edge, f'{edge.capitalize()} edge of the area, in degrees.', NUMBER, required=True)
                          for edge in ('north', 'south', 'east', 'west'))],
         'response': obj({'copyright': leaf('string', 'The data credit to show under the map, such as "Map data ©2026 Google".'),
                          'maxZoomRects': arr(obj({'maxZoom': leaf('integer', 'Deepest zoom with tiles in this rectangle.'),
                                                   'north': leaf('number', 'North edge, degrees.'),
                                                   'south': leaf('number', 'South edge, degrees.'),
                                                   'east': leaf('number', 'East edge, degrees.'),
                                                   'west': leaf('number', 'West edge, degrees.')}),
                                              'Rectangles of the area and the deepest zoom each has tiles for.')},
                         'Viewport information.', ('copyright',))},
    ],
}


VENDORS: Mapping[str, Dict[str, Any]] = {
    'usgs_earthquakes': USGS,
    'our_world_in_data': OWID,
    'eurostat': EUROSTAT,
    'who_gho': WHO,
    'taiwan_cwa': CWA,
    'taiwan_moenv': MOENV,
    'nasa_gibs': GIBS,
    'eox_cloudless': EOX,
    'openstreetmap': OSM,
    'versatiles': VERSATILES,
    'stadia_maps': STADIA,
    'maptiler': MAPTILER,
    'thunderforest': THUNDERFOREST,
    'google_maps': GOOGLE_MAPS,
}
