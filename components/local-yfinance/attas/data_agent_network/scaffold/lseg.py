"""Executable LSEG Data Platform REST inventory.

The route families mirror the public content configuration shipped with the
official ``lseg-data`` Python library.  Only retrieval and analytics requests
belong here: account administration, OAuth, streaming, and mutating custom
instrument/curve-definition operations are deliberately outside ``data_fetch``.
"""

from __future__ import annotations

from typing import Any, Mapping


def _parameter(
    name: str,
    *,
    location: str = "query",
    required: bool = False,
    schema: Mapping[str, Any] | None = None,
    description: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "location": location,
        "required": required,
        "schema": dict(schema or {"type": "string"}),
        "description": description,
    }


def _endpoint(
    identity: str,
    name: str,
    operation: str,
    category: str,
    *,
    method: str = "GET",
    parameters: tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    return {
        "identity": identity,
        "name": name,
        "operation": operation,
        "category": category,
        "method": method,
        "parameters": [dict(item) for item in parameters],
    }


UNIVERSE = _parameter(
    "universe",
    required=True,
    description="LSEG instrument universe, such as one or more RICs or PermIDs.",
)
PATH_UNIVERSE = _parameter(
    "universe",
    location="path",
    required=True,
    description="LSEG instrument identifier used in the route path.",
)
BODY = _parameter(
    "body",
    location="body",
    required=True,
    schema={"type": "object", "additionalProperties": True},
    description="Provider-documented JSON request body.",
)
OPTIONAL_BODY = _parameter(
    "body",
    location="body",
    schema={"type": "object", "additionalProperties": True},
    description="Provider-documented JSON request body.",
)


def _historical_pricing() -> list[dict[str, Any]]:
    common = (
        PATH_UNIVERSE,
        _parameter("interval"),
        _parameter("start"),
        _parameter("end"),
        _parameter("count", schema={"type": "integer"}),
        _parameter("fields"),
        _parameter("adjustments"),
        _parameter("sessions"),
    )
    return [
        _endpoint(
            (
                "lseg-historical-pricing-interday"
                if view == "interday-summaries"
                else "lseg-historical-pricing-intraday"
                if view == "intraday-summaries"
                else "lseg-historical-pricing-events"
            ),
            f"Historical Pricing {label}",
            f"/data/historical-pricing/v1/views/{view}/{{universe}}",
            "Historical Pricing",
            parameters=common,
        )
        for view, label in (
            ("events", "Intraday Events"),
            ("interday-summaries", "Interday Summaries"),
            ("intraday-summaries", "Intraday Summaries"),
        )
    ]


def _analytics() -> list[dict[str, Any]]:
    post_routes = (
        ("add-periods", "Add Periods", "/data/quantitative-analytics-dates-and-calendars/v1/add-periods", "Dates and Calendars"),
        ("holidays", "Holidays", "/data/quantitative-analytics-dates-and-calendars/v1/holidays", "Dates and Calendars"),
        ("count-periods", "Count Periods", "/data/quantitative-analytics-dates-and-calendars/v1/count-periods", "Dates and Calendars"),
        ("date-schedule", "Date Schedule", "/data/quantitative-analytics-dates-and-calendars/v1/date-schedule", "Dates and Calendars"),
        ("is-working-day", "Is Working Day", "/data/quantitative-analytics-dates-and-calendars/v1/is-working-day", "Dates and Calendars"),
        ("financial-contracts", "Financial Contracts", "/data/quantitative-analytics/v1/financial-contracts", "Instrument Pricing Analytics"),
        ("async-financial-contracts", "Asynchronous Financial Contracts", "/data/quantitative-analytics/v1/async-financial-contracts", "Instrument Pricing Analytics"),
        ("bond-curves", "Bond Curves", "/data/quantitative-analytics-curves-and-surfaces/v1/curves/bond-curves/curves", "Curves and Surfaces"),
        ("cross-currency-curves", "Cross Currency Curves", "/data/quantitative-analytics-curves-and-surfaces/v1/curves/cross-currency-curves/curves", "Curves and Surfaces"),
        ("cross-currency-definitions-get", "Get Cross Currency Curve Definitions", "/data/quantitative-analytics-curves-and-surfaces/v1/curves/cross-currency-curves/definitions/get", "Curves and Surfaces"),
        ("cross-currency-definitions-search", "Search Cross Currency Curve Definitions", "/data/quantitative-analytics-curves-and-surfaces/v1/curves/cross-currency-curves/definitions/search", "Curves and Surfaces"),
        ("cross-currency-triangulate-definitions", "Search Cross Currency Triangulation Definitions", "/data/quantitative-analytics-curves-and-surfaces/v1/curves/cross-currency-curves/triangulate-definitions/search", "Curves and Surfaces"),
        ("forward-curves", "Forward Curves", "/data/quantitative-analytics-curves-and-surfaces/v1/curves/forward-curves", "Curves and Surfaces"),
        ("surfaces", "Surfaces", "/data/quantitative-analytics-curves-and-surfaces/v1/surfaces", "Curves and Surfaces"),
        ("async-surfaces", "Asynchronous Surfaces", "/data/quantitative-analytics-curves-and-surfaces/v1/async-surfaces", "Curves and Surfaces"),
        ("zero-coupon-curves", "Zero Coupon Curves", "/data/quantitative-analytics-curves-and-surfaces/v1/curves/zc-curves", "Curves and Surfaces"),
        ("zero-coupon-curve-definitions", "Zero Coupon Curve Definitions", "/data/quantitative-analytics-curves-and-surfaces/v1/curves/zc-curve-definitions", "Curves and Surfaces"),
    )
    endpoints = [
        _endpoint(f"lseg-{identity}", name, path, category, method="POST", parameters=(BODY,))
        for identity, name, path, category in post_routes
    ]
    endpoints.extend(
        (
            _endpoint(
                "lseg-financial-contract-operation",
                "Financial Contract Async Operation",
                "/data/quantitative-analytics/v1/async-operation/{operation_id}",
                "Instrument Pricing Analytics",
                parameters=(_parameter("operation_id", location="path", required=True),),
            ),
            _endpoint(
                "lseg-financial-contract-resource",
                "Financial Contract Async Resource",
                "/data/quantitative-analytics/v1/async-resource/{resource_id}",
                "Instrument Pricing Analytics",
                parameters=(_parameter("resource_id", location="path", required=True),),
            ),
            _endpoint(
                "lseg-curves-surfaces-operation",
                "Curves and Surfaces Async Operation",
                "/data/quantitative-analytics-curves-and-surfaces/v1/async-operation/{operation_id}",
                "Curves and Surfaces",
                parameters=(_parameter("operation_id", location="path", required=True),),
            ),
            _endpoint(
                "lseg-curves-surfaces-resource",
                "Curves and Surfaces Async Resource",
                "/data/quantitative-analytics-curves-and-surfaces/v1/async-resource/{resource_id}",
                "Curves and Surfaces",
                parameters=(_parameter("resource_id", location="path", required=True),),
            ),
        )
    )
    return endpoints


def _news_and_esg() -> list[dict[str, Any]]:
    return [
        _endpoint("lseg-news-headlines", "News Headlines", "/data/news/v1/headlines", "News", parameters=tuple(_parameter(name) for name in ("query", "limit", "date_from", "date_to", "cursor"))),
        _endpoint("lseg-news-story", "News Story", "/data/news/v1/stories/{story_id}", "News", parameters=(_parameter("story_id", location="path", required=True), _parameter("Accept", location="header"))),
        _endpoint("lseg-top-news", "Top News", "/data/news/v1/top-news", "News", parameters=(_parameter("page_size", schema={"type": "integer"}), _parameter("main_coverage"), _parameter("custom_instruments"))),
        _endpoint("lseg-news-image", "News Image", "/data/news/v1/images/{image_id}", "News", parameters=(_parameter("image_id", location="path", required=True), _parameter("width", schema={"type": "integer"}), _parameter("height", schema={"type": "integer"}), _parameter("Accept", location="header"))),
        _endpoint("lseg-news-online-report", "News Online Report", "/data/news/v1/online-reports/{report_id}", "News", parameters=(_parameter("report_id", location="path", required=True), _parameter("fullContent", schema={"type": "boolean"}))),
        _endpoint("lseg-esg-universe", "ESG Universe", "/data/environmental-social-governance/v2/universe", "ESG", parameters=(_parameter("format"),)),
        *[
            _endpoint(
                f"lseg-esg-{view}",
                f"ESG {view.replace('-', ' ').title()}",
                f"/data/environmental-social-governance/v2/views/{view}",
                "ESG",
                parameters=(UNIVERSE, _parameter("format"), _parameter("start", schema={"type": "integer"}), _parameter("end", schema={"type": "integer"})),
            )
            for view in ("basic", "measures-full", "measures-standard", "scores-full", "scores-standard")
        ],
    ]


def _ownership() -> list[dict[str, Any]]:
    views = {
        "consolidated": ("breakdown", "concentration", "investors", "recent-activity", "shareholders-history-report", "shareholders-report", "top-n-concentration"),
        "fund": ("breakdown", "concentration", "investors", "holdings", "recent-activity", "shareholders-history-report", "shareholders-report", "top-n-concentration"),
        "insider": ("shareholders-report", "transaction-report"),
        "investor": ("holdings",),
    }
    endpoints = [
        _endpoint(
            f"lseg-ownership-{group}-{view}",
            f"Ownership {group.title()} {view.replace('-', ' ').title()}",
            f"/data/ownership/v1/views/{group}/{view}",
            "Ownership",
            parameters=(UNIVERSE, _parameter("start"), _parameter("end"), _parameter("limit", schema={"type": "integer"})),
        )
        for group, group_views in views.items()
        for view in group_views
    ]
    endpoints.append(
        _endpoint("lseg-ownership-org-info", "Ownership Organization Information", "/data/ownership/v1/views/org-info", "Ownership", parameters=(UNIVERSE,))
    )
    return endpoints


def _estimates() -> list[dict[str, Any]]:
    routes = (
        "view-actuals/annual",
        "view-actuals/interim",
        "view-actuals-kpi/annual",
        "view-actuals-kpi/interim",
        "view-summary/annual",
        "view-summary/historical-snapshots-non-periodic-measures",
        "view-summary/historical-snapshots-periodic-measures-annual",
        "view-summary/historical-snapshots-periodic-measures-interim",
        "view-summary/historical-snapshots-recommendations",
        "view-summary/interim",
        "view-summary/non-periodic-measures",
        "view-summary/recommendations",
        "view-summary-kpi/annual",
        "view-summary-kpi/historical-snapshots-kpi",
        "view-summary-kpi/interim",
    )
    return [
        _endpoint(
            f"lseg-estimates-{route.replace('/', '-')}",
            f"Estimates {route.replace('/', ' ').replace('-', ' ').title()}",
            f"/data/estimates/v1/{route}",
            "Estimates",
            parameters=(UNIVERSE, _parameter("measures"), _parameter("period"), _parameter("start"), _parameter("end")),
        )
        for route in routes
    ]


def _platform_services() -> list[dict[str, Any]]:
    endpoints = [
        _endpoint("lseg-pricing-snapshots", "Pricing Snapshots", "/data/pricing/snapshots/v1/", "Market Data", parameters=(UNIVERSE, _parameter("fields"))),
        _endpoint("lseg-pricing-chains", "Pricing Chains", "/data/pricing/chains/v1/", "Market Data", parameters=(UNIVERSE, _parameter("display_template"), _parameter("skip", schema={"type": "integer"}), _parameter("top", schema={"type": "integer"}))),
        _endpoint("lseg-datagrid", "DataGrid Fundamental and Reference Data", "/data/datagrid/beta1/", "Fundamental and Reference Data", method="POST", parameters=(BODY,)),
        _endpoint("lseg-filings-retrieval", "Filings Retrieval", "/data/filings/v1/retrieval", "Filings", method="POST", parameters=(BODY,)),
        _endpoint("lseg-instrument-search", "Instrument Search", "/discovery/search/v1/", "Discovery", method="POST", parameters=(BODY,)),
        _endpoint("lseg-symbol-lookup", "Symbol Lookup", "/discovery/search/v1/lookup", "Symbology", method="POST", parameters=(BODY,)),
        _endpoint("lseg-search-metadata-views", "Search Metadata Views", "/discovery/search/v1/metadata/views", "Discovery", parameters=(_parameter("view"),)),
        _endpoint("lseg-data-store-graphql", "Data Store GraphQL", "/data-store/v1/graphql", "Data Store", method="POST", parameters=(BODY,)),
        _endpoint(
            "lseg-funds-assets-get",
            "Fund Assets",
            "/data/funds/v1/assets",
            "Funds",
            parameters=tuple(
                _parameter(name)
                for name in ("symbols", "properties", "screen", "page", "pageSize", "sort")
            ),
        ),
        _endpoint(
            "lseg-funds-assets-post",
            "Fund Assets Batch Request",
            "/data/funds/v1/assets",
            "Funds",
            method="POST",
            parameters=(BODY,),
        ),
        _endpoint(
            "lseg-funds-asset",
            "Fund Asset",
            "/data/funds/v1/assets/{asset_id}",
            "Funds",
            parameters=(
                _parameter("asset_id", location="path", required=True),
                _parameter("properties"),
            ),
        ),
        _endpoint(
            "lseg-funds-reference-datapoints",
            "Fund Reference Datapoints",
            "/data/funds/v1/assets/ref/datapoints",
            "Funds",
            parameters=(_parameter("properties"),),
        ),
    ]
    for resource in ("buckets", "file-sets", "files", "packages"):
        endpoints.append(
            _endpoint(
                f"lseg-file-store-{resource}",
                f"File Store {resource.replace('-', ' ').title()}",
                f"/file-store/v1/{resource}",
                "Client File Store",
                parameters=tuple(_parameter(name) for name in ("bucket", "package", "fileSet", "cursor", "limit")),
            )
        )
    endpoints.extend(
        (
            _endpoint("lseg-custom-instrument", "Get Custom Instrument", "/data/custom-instruments/v1/instruments/{universe}", "Custom Instruments", parameters=(PATH_UNIVERSE,)),
        _endpoint("lseg-custom-instrument-search", "Search Custom Instruments", "/data/custom-instruments/v1/search", "Custom Instruments", parameters=tuple(_parameter(name) for name in ("access", "type", "cursor", "limit"))),
            *[
                _endpoint(
                    f"lseg-custom-instrument-{view}",
                    f"Custom Instrument {view.replace('-', ' ').title()}",
                    f"/data/custom-instruments/v1/{view}/{{universe}}",
                    "Custom Instruments",
                    parameters=(PATH_UNIVERSE, _parameter("interval"), _parameter("start"), _parameter("end"), _parameter("count", schema={"type": "integer"}), _parameter("fields")),
                )
                for view in ("events", "interday-summaries", "intraday-summaries")
            ],
        )
    )
    return endpoints


LSEG_DATA_ENDPOINTS: tuple[Mapping[str, Any], ...] = tuple(
    [
        *_historical_pricing(),
        *_platform_services(),
        *_news_and_esg(),
        *_ownership(),
        *_estimates(),
        *_analytics(),
    ]
)
