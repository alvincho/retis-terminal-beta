"""Finance meaning and application profiles, independent of data providers."""
from functools import lru_cache
import copy

from prompits.core.resource_registry import reference
from prompits.core.pulse_models import validate_pds

NAMESPACE = 'ai.attas.finance'

# Each field has a portable definition. Profile membership never silently
# equates a provider's similarly named value to this concept.
FIELDS = {
    'ticker': ('string', 'identifier', 'Yahoo-style listing identifier; exchange suffix distinguishes listings.'),
    'name': ('string', 'text', 'Source-supplied name of the issuer or instrument.'),
    'nameZh': ('string', 'text', 'Source-supplied Chinese name; content is not machine translated.'),
    'nameEn': ('string', 'text', 'Source-supplied English name; content is not machine translated.'),
    'last': ('number', 'quote-currency', 'Latest available provider price; priceKind identifies trade, close or trial semantics.'),
    'priceKind': ('string', 'classification', 'Whether the observed price is a trade, closing value, trial price or unspecified provider price.'),
    'change': ('number', 'quote-currency', 'Price difference from the provider-declared comparison close.'),
    'changePercent': ('number', 'percent', 'Price change as percentage points relative to the comparison close, with 1 meaning one percent.'),
    'volume': ('number', 'declared-quantity', 'Traded quantity over the declared interval or session; volumeUnit and volumeScope are mandatory dimensions.'),
    'volumeUnit': ('string', 'classification', 'Unit of the quantity: shares, contracts, lots, amount, or explicitly unknown.'),
    'volumeScope': ('string', 'classification', 'Whether volume describes an interval, session cumulative total or a single trade.'),
    'currency': ('string', 'ISO-4217', 'Currency in which the reported monetary value is denominated.'),
    'observedAt': ('string', 'ISO-8601', 'Time of the provider observation, distinct from retrieval time.'),
    'sourceUpdatedAt': ('string', 'ISO-8601', 'Provider update time for the whole record, which may reflect book activity rather than a new trade.'),
    'timestamp': ('string', 'ISO-8601', 'Source observation interval date or timezone-qualified timestamp; a date is not an invented UTC instant.'),
    'open': ('number', 'quote-currency', 'First transaction price in the declared interval and adjustment basis.'),
    'high': ('number', 'quote-currency', 'Highest transaction price in the declared interval and adjustment basis.'),
    'low': ('number', 'quote-currency', 'Lowest transaction price in the declared interval and adjustment basis.'),
    'close': ('number', 'quote-currency', 'Last transaction price in the declared interval and adjustment basis.'),
    'adjustedClose': ('number', 'quote-currency', 'Closing price adjusted by the source method for corporate actions.'),
    'adjustmentBasis': ('string', 'classification', 'Declared raw or corporate-action-adjusted price basis.'),
    'marketCap': ('number', 'currency', 'Issuer market capitalization at the source observation time and stated currency.'),
    'epsTtm': ('number', 'currency-per-share', 'Source-reported trailing-twelve-month EPS; epsBasis records diluted/basic or unspecified basis.'),
    'epsBasis': ('string', 'classification', 'EPS accounting basis, such as diluted, basic, or source-unspecified.'),
    'priceEarnings': ('number', 'ratio', 'Market price divided by the source-declared earnings-per-share basis.'),
    'priceBook': ('number', 'ratio', 'Market price divided by source-declared book value per share.'),
    'dividendYieldPercent': ('number', 'percent', 'Source dividend yield in percentage points, preserving its trailing or forward basis.'),
    'fiftyTwoWeekHigh': ('number', 'quote-currency', 'Highest source price in its trailing 52-week window.'),
    'fiftyTwoWeekLow': ('number', 'quote-currency', 'Lowest source price in its trailing 52-week window.'),
    'sector': ('string', 'classification', 'Economic sector under the source classification system.'),
    'industry': ('string', 'classification', 'Industry under the source classification system.'),
    'exchange': ('string', 'identifier', 'Venue or listing exchange reported by the provider.'),
    'market': ('string', 'identifier', 'Market segment reported by the provider.'),
    'country': ('string', 'classification', 'Source-reported country of the issuer.'),
    'website': ('string', 'URI', 'Issuer website URL provided by the source.'),
    'description': ('string', 'text', 'Source-provided business or instrument description.'),
    'period': ('string', 'period', 'Reporting period label or period-end date; periodType distinguishes frequency.'),
    'periodType': ('string', 'classification', 'Reported fiscal period frequency or source-defined forecast horizon.'),
    'asOf': ('string', 'ISO-8601', 'Time at which the estimate or information set was observed.'),
    'revenue': ('number', 'reporting-currency', 'Reported revenue for the identified fiscal period and reporting currency.'),
    'netIncome': ('number', 'reporting-currency', 'Reported net income for the identified fiscal period and reporting currency.'),
    'freeCashFlow': ('number', 'reporting-currency', 'Source-reported free cash flow for the fiscal period, under its stated method.'),
    'revenueGrowth': ('number', 'ratio', 'Source revenue growth ratio against its comparison period; 0.1 means ten percent.'),
    'operatingMargin': ('number', 'ratio', 'Operating income divided by revenue for the declared period.'),
    'headline': ('string', 'text', 'Original source headline of a news item.'),
    'publishedAt': ('string', 'ISO-8601', 'Publication timestamp assigned by the original content source.'),
    'url': ('string', 'URI', 'Content location supplied by the publisher.'),
    'summary': ('string', 'text', 'Source-supplied summary or excerpt, without automatic translation.'),
    'publisher': ('string', 'text', 'Identified publisher of the content.'),
    'instrumentReferences': ('array', 'identifier', 'Listings explicitly associated with the content.'),
    'eventAt': ('string', 'ISO-8601', 'Time or date of the corporate announcement or scheduled event.'),
    'reportedEps': ('number', 'currency-per-share', 'EPS reported for the specified event or fiscal period, with accounting basis retained.'),
    'estimatedEps': ('number', 'currency-per-share', 'Consensus EPS estimate for the specified forecast period and as-of information set.'),
    'reportedRevenue': ('number', 'reporting-currency', 'Revenue reported for the specified fiscal period.'),
    'estimatedRevenue': ('number', 'reporting-currency', 'Consensus revenue estimate for the specified forecast period and as-of information set.'),
    'surprisePercent': ('number', 'percent', 'Reported earnings surprise relative to the source consensus in percentage points.'),
    'comment': ('string', 'text', 'Generated analyst commentary tied to explicit input content and method.'),
    'video': ('object', 'artifact-reference', 'Reference to a generated video artifact and its lineage.'),
    'documentReference': ('string', 'artifact-reference', 'Identifier of an accessible report artifact.'),
    'bid': ('number', 'quote-currency', 'Displayed bid price at the specified book depth; level one or an omitted level denotes the best bid.'),
    'ask': ('number', 'quote-currency', 'Displayed ask price at the specified book depth; level one or an omitted level denotes the best ask.'),
    'bidSize': ('number', 'declared-quantity', 'Displayed bid quantity with an explicit size unit.'),
    'askSize': ('number', 'declared-quantity', 'Displayed ask quantity with an explicit size unit.'),
    'level': ('integer', 'ordinal', 'Order-book depth rank starting at one.'),
}

PROFILES = {
    'market.quotes.v1': ['ticker','last','priceKind','change','changePercent','volume','volumeUnit','volumeScope','currency','observedAt','sourceUpdatedAt','exchange','market','bid','ask'],
    'market.history.v1': ['ticker','timestamp','open','high','low','close','adjustedClose','adjustmentBasis','volume','volumeUnit','volumeScope','currency'],
    'company.profile.v1': ['ticker','name','nameZh','nameEn','sector','industry','country','website','description','currency','marketCap','epsTtm','epsBasis','priceEarnings','priceBook','dividendYieldPercent','fiftyTwoWeekHigh','fiftyTwoWeekLow'],
    'company.financials.v1': ['ticker','period','periodType','revenue','netIncome','freeCashFlow','currency'],
    'company.peers.v1': ['ticker','period','marketCap','priceEarnings','revenueGrowth','operatingMargin','currency'],
    'research.news.v1': ['ticker','headline','publishedAt','url','publisher','summary','instrumentReferences'],
    'company.earnings.v1': ['ticker','eventAt','period','reportedEps','reportedRevenue','surprisePercent','currency','epsBasis'],
    'company.estimates.v1': ['ticker','period','periodType','asOf','estimatedEps','estimatedRevenue','currency','epsBasis'],
    'research.commentary.v1': ['ticker','comment','publishedAt','publisher','instrumentReferences'],
    'research.video-commentary.v1': ['ticker','comment','video','publishedAt','publisher'],
    'research.reports.v1': ['ticker','headline','documentReference','publishedAt','publisher','url','summary'],
    'market.orderbook.v1': ['ticker','level','bid','ask','bidSize','askSize','volumeUnit','observedAt'],
    'market.trades.v1': ['ticker','last','priceKind','volume','volumeUnit','volumeScope','observedAt'],
}


from .research_models import FIELDS as RESEARCH_FIELDS, PROFILES as RESEARCH_PROFILES
FIELDS.update(RESEARCH_FIELDS)
PROFILES.update(RESEARCH_PROFILES)


def value_schema(field):
    kind = FIELDS[field][0]
    result = {'type': [kind, 'null']}
    if kind == 'array':
        result['items'] = {'type': 'string'}
    return result


@lru_cache(maxsize=1)
def _resources():
    common = {'pds_version':'0.2.0','namespace':NAMESPACE,'version':'1.0.0','status':'stable'}
    resources = []
    base = {**common,'resource_type':'pulse_definition','id':NAMESPACE+'.observation_set','title':'Financial observations',
            'description':'A set of financial observations with explicit instruments, time, units and provenance.',
            'pulse_class':'fact','concept':{'definition':'A collection of financial observations whose field semantics are constrained by a profile.',
                                          'entity_types':['financial_observation'],'dimensions':['instrument','time','currency','basis']},
            'interface':{'schema_language':'json-schema-2020-12','request_schema':{'type':'object'},
                         'response_schema':{'type':'array','items':{'type':'object'}}}}
    resources.append(base)
    field_refs = {}
    for field, (kind, unit, meaning) in FIELDS.items():
        artifact = field in ('comment','video','documentReference')
        analytic = field in ('smaFast','smaSlow','rsi','signal','matched','returnPercent','drawdownPercent','tradeCount')
        value = {**common,'resource_type':'pulse_definition','id':NAMESPACE+'.field.'+field,'title':field,
                 'description':meaning,'pulse_class':'artifact' if artifact else 'analytic' if analytic else 'fact',
                 'concept':{'definition':meaning,'entity_types':['financial_observation'],
                            'dimensions':['instrument','time','currency','basis'],'units':{'default_unit':unit}},
                 'interface':{'schema_language':'json-schema-2020-12','request_schema':{'type':'object'},'response_schema':value_schema(field)}}
        if artifact or analytic:
            value['derivation']={'method_type':'publisher-declared','input_pulse_ids':[base['id']],'notes':'Generation or publication method and actual inputs accompany each artifact.'}
        resources.append(value);field_refs[field]=reference(value)
    for schema, fields in PROFILES.items():
        resources.append({**common,'resource_type':'pulse_profile','id':NAMESPACE+'.profile.'+schema,
                          'title':schema,'description':'Source-independent display requirements for '+schema,
                          'base_ref':reference(base),'fields':{f:field_refs[f] for f in fields},
                          'dependencies':[reference(base),*[field_refs[f] for f in fields]],
                          'constraints':{'joinKeys':['ticker'],'missing':'explicit','contentTranslation':False},
                          'interface':{'schema_language':'json-schema-2020-12','request_schema':{'type':'object'},
                                       'response_schema':{'type':'array','maxItems':10000,'items':{'type':'object','properties':{f:value_schema(f) for f in fields}}}},
                          'extensions':{'ai.attas:legacySchema':schema}})
    for value in resources:
        validate_pds(value)
    return resources


def resources():
    return copy.deepcopy(_resources())


def profile(schema):
    return next((copy.deepcopy(r) for r in _resources() if r['resource_type']=='pulse_profile' and r.get('extensions',{}).get('ai.attas:legacySchema')==schema), None)


def model_requirement(requirement):
    result = copy.deepcopy(requirement)
    model = profile(result.get('schema'))
    if model and not result.get('modelRef') and set(result.get('fields', [])) <= set(model['fields']):
        result['modelRef'] = reference(model)
        result['modelContract'] = 'model-requirement/1'
        keys = {'market.history.v1':['ticker','timestamp'], 'research.news.v1':['url'],
                'company.financials.v1':['ticker','period'],'company.estimates.v1':['ticker','period'],
                'company.earnings.v1':['ticker','eventAt'],
                'company.institutional.v1':['ticker','timestamp','investor'],
                'company.margin.v1':['ticker','timestamp'],
                'company.statement-items.v1':['ticker','period','metric'],
                'company.distributions.v1':['ticker','eventAt'],
                'company.events.v1':['ticker','eventAt','eventType'],
                'fund.holdings.v1':['ticker','holdingId','asOf'],
                'market.orderbook.v1':['ticker','level'],
                'market.trades.v1':['ticker','observedAt'],
                'research.reports.v1':['documentReference'],
                **{key:['ticker','timestamp'] for key in ('research.indicators.v1','research.screen.v1','research.backtest.v1','research.alerts.v1')}
                }.get(result.get('schema'), ['ticker'])
        result['join'] = {'keys':keys, 'dimensions':['currency'], 'maxSkewSeconds':300 if result.get('schema')=='market.quotes.v1' else 86400}
    return result
