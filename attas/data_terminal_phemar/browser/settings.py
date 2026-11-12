"""Generic bounds for settings on a supplied Phema container."""
import copy
import re
from fastapi import HTTPException

def saved_settings(phema, entry, value):
    from .engine import BrowserEngine
    from .nested import project
    from ..content_policy import validate_overrides
    allowed={'fieldBindings','refreshOverrides','contentOverrides','instrumentOverrides',
             'parameterValues','range','layoutTabs','uiLanguage'}
    if not isinstance(value,dict) or set(value)-allowed:
        raise HTTPException(422,'Provide Browser settings without credentials or session data.')
    instruments=value.get('instrumentOverrides',{})
    parameters=value.get('parameterValues',{})
    if not isinstance(instruments,dict) or not isinstance(parameters,dict):
        raise HTTPException(422,'Browser parameters and instrument overrides must be objects.')
    projected,_,_=project(phema,instruments,parameters)
    tabs=value.get('layoutTabs',{})
    if not isinstance(tabs,dict) or len(tabs)>64 or any(not isinstance(k,str) or len(k)>160 or not isinstance(v,str) or not re.fullmatch(r'[\w-]{1,80}',v) for k,v in tabs.items()):
        raise HTTPException(422,'Invalid layout tabs.')
    span=value.get('range','1M');language=value.get('uiLanguage')
    if span not in ('1D','1W','1M','3M','1Y') or language not in (None,'en','zh-Hant','zh-Hans','ja','es','fr'):
        raise HTTPException(422,'Invalid Browser range or UI language.')
    return {'fieldBindings':{},
            'refreshOverrides':BrowserEngine.validate_refresh_overrides(value.get('refreshOverrides',{}),projected),
            'contentOverrides':validate_overrides(phema,value.get('contentOverrides',{})),
            'instrumentOverrides':copy.deepcopy(instruments),'parameterValues':copy.deepcopy(parameters),
            'range':span,'layoutTabs':copy.deepcopy(tabs),'uiLanguage':language}

