"""Versioned, data-only contracts shared by media validators and publishers."""
from copy import deepcopy

TEXT = {'type':'string', 'maxLength':200000}
URL = {'type':'string', 'pattern':r'^https://[^/@\s]+(?:/[^\s]*)?$', 'maxLength':4096}
VECTOR = {'type':'array','minItems':3,'maxItems':3,'items':{'type':'number','minimum':-10000,'maximum':10000}}

def obj(properties, required=(), **kw):
    return {'type':'object','properties':properties,'required':list(required), **kw}

TIMELINE = obj({'duration':{'type':'number','exclusiveMinimum':0,'maximum':86400},'loop':{'type':'boolean'},'tracks':{'type':'array','maxItems':100,'items':obj({'target':{'type':'string','maxLength':80},'property':{'enum':['position','rotation','size']},'keyframes':{'type':'array','minItems':2,'maxItems':500,'items':obj({'time':{'type':'number','minimum':0,'maximum':86400},'value':VECTOR},['time','value'],additionalProperties=False)}},['target','property','keyframes'],additionalProperties=False)}}, ['duration','tracks'], additionalProperties=False)
SCENE_OBJECT = obj({'id':{'type':'string','pattern':'^[a-zA-Z0-9_-]{1,80}$'},'shape':{'enum':['box','sphere','mesh']},'position':VECTOR,'rotation':VECTOR,'size':VECTOR,'spin':{'type':'number','minimum':-10,'maximum':10},'color':{'type':'string','pattern':'^#[0-9a-fA-F]{6}$'},'vertices':{'type':'array','maxItems':10000,'items':VECTOR},'faces':{'type':'array','maxItems':20000,'items':{'type':'array','minItems':3,'maxItems':4,'items':{'type':'integer','minimum':0,'maximum':9999}}}}, ['shape'], additionalProperties=False)
MEDIA_SCHEMAS = {
 'text':obj({'text':TEXT,'title':TEXT,'url':URL},['text']),
 'image':obj({'url':URL,'alt':TEXT,'title':TEXT},['url']),
 'scene':obj({'description':TEXT,'objects':{'type':'array','maxItems':100,'items':SCENE_OBJECT},'timeline':TIMELINE,'asset':obj({'url':URL,'format':{'enum':['gltf','glb']},'version':{'type':'string','minLength':1},'animation':{'type':'string'},'sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'}},['url','format','version'])},['objects']),
 'interactive':obj({'title':TEXT,'description':TEXT,'inputSchema':{'type':'object'},'submitLabel':{'type':'string','maxLength':80},'result':{}},['inputSchema']),
}
for kind in ('audio','video'):
 MEDIA_SCHEMAS[kind]=obj({'url':URL,'title':TEXT,'poster':URL,'transcript':TEXT,'captions':{'type':'array','maxItems':32,'items':obj({'url':URL,'language':{'type':'string','maxLength':35},'label':{'type':'string','maxLength':100}},['url','language'])},'playback':obj({'start':{'type':'number','minimum':0},'end':{'type':'number','minimum':0},'loop':{'type':'boolean'},'muted':{'type':'boolean'},'rate':{'type':'number','minimum':0.25,'maximum':4}},additionalProperties=False)},['url'])

CUSTOM_SCHEMAS = {
 'phemacast:display.table':obj({'columns':{'type':'array','minItems':1,'maxItems':50,'items':obj({'key':{'type':'string','maxLength':100},'label':{'type':'string','maxLength':100}},['key'])},'rows':{'type':'array','maxItems':10000,'items':{'type':'object'}}},['columns','rows']),
 'phemacast:display.key-values':obj({'values':{'type':'object','maxProperties':200}},['values'])
}

def media_schema(kind):
 return deepcopy(MEDIA_SCHEMAS[kind])
