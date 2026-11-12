"""Bounded declarative media; neither publications nor registries install code."""
from urllib.parse import urlsplit
import math
from jsonschema import Draft202012Validator
from .composition_contracts import MEDIA_SCHEMAS, CUSTOM_SCHEMAS

MEDIA = {'text', 'image', 'audio', 'video', 'scene', 'interactive'}


def media_url(value):
    if not isinstance(value, str) or len(value) > 4096: raise ValueError('Invalid media URL.')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Media requires an HTTPS asset URL without embedded credentials.')
    return value


def validate_media(value, kind):
    if not isinstance(value, dict) or kind not in MEDIA: raise ValueError('Invalid media value.')
    errors = list(Draft202012Validator(MEDIA_SCHEMAS[kind]).iter_errors(value))
    if errors: raise ValueError('Invalid '+kind+' media: '+errors[0].message)
    if kind == 'interactive':
        from .composition import schema_check
        schema_check(value['inputSchema'])
    elif kind == 'text':
        if not isinstance(value.get('text'), str) or len(value['text']) > 200_000: raise ValueError('Text media requires bounded text.')
    elif kind == 'scene':
        objects = value.get('objects', [])
        if not isinstance(objects, list) or len(objects) > 100: raise ValueError('Scene supports up to 100 objects.')
        for obj in objects:
            if not isinstance(obj, dict) or obj.get('shape') not in ('box', 'sphere', 'mesh'): raise ValueError('Unsupported declarative shape.')
            for key in ('position', 'rotation', 'size'):
                vector = obj.get(key, [1, 1, 1] if key == 'size' else [0, 0, 0])
                if not isinstance(vector, list) or len(vector) != 3 or any(type(n) not in (float,int) or not math.isfinite(n) or abs(n) > 10000 for n in vector): raise ValueError('Invalid scene transform.')
            if 'spin' in obj and (type(obj['spin']) not in (int,float) or not math.isfinite(obj['spin']) or abs(obj['spin']) > 10): raise ValueError('Scene spin exceeds bounds.')
        ids=[o.get('id') for o in objects if o.get('id')]
        if len(ids)!=len(set(ids)):raise ValueError('Scene object IDs must be unique.')
        for o in objects:
            if o['shape']=='mesh' and (not o.get('vertices') or not o.get('faces') or any(i>=len(o['vertices']) for face in o['faces'] for i in face)):raise ValueError('Mesh faces require valid vertices.')
        for track in value.get('timeline',{}).get('tracks',[]):
            times=[k['time'] for k in track['keyframes']]
            if track['target'] not in ids or times!=sorted(set(times)) or times[-1]>value['timeline']['duration']:raise ValueError('Invalid animation timeline.')
        if value.get('asset'):media_url(value['asset']['url'])
    else:
        media_url(value.get('url'))
        tracks=value.get('captions', [])
        if not isinstance(tracks,list) or len(tracks)>32: raise ValueError('Media supports up to 32 caption tracks.')
        if value.get('poster'): media_url(value['poster'])
        for track in tracks:
            if not isinstance(track, dict) or not isinstance(track.get('language'), str): raise ValueError('Caption language required.')
            media_url(track.get('url'))
        if kind == 'image' and not isinstance(value.get('alt', ''), str): raise ValueError('Image description must be text.')
    return value


def validate_custom(value, capability):
    schema=CUSTOM_SCHEMAS.get(capability.get('id'))
    if schema is None:
        from .renderer_plugins import installed_renderers
        installed=next((r for r in installed_renderers() if r['capability']['id']==capability.get('id')),None)
        schema=installed['inputSchema'] if installed else None
    if schema is None:return value
    from .schema_security import validate
    from jsonschema.exceptions import ValidationError
    try:validate(schema, value)
    except ValidationError as exc:raise ValueError('Renderer input does not match its contract.') from exc
    return value
