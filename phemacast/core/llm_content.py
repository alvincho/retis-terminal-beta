"""Vendor-neutral LLM message content: text, pictures and one web page to read, and how a published endpoint writes them.

A message's `content` is a string (plain text, as before) or a list of parts:

- `{"type": "text", "text": "..."}`
- `{"type": "image", "mediaType": "image/png" | "image/jpeg" | "image/webp" | "image/gif", "data": <base64>}`
- `{"type": "image", "url": "https://..."}` (the LLM vendor fetches the picture)

A request may also ask the model to read one web page (`webFetch`: {"url": "https://..."}); the LLM vendor fetches
it, never the caller's computer or an agent.

How a vendor's wire format writes these is not known here: the LLM agent publishes it with its endpoint as data
(`parts`, `webFetch`, `textBlocks`), so a Terminal running the endpoint with a viewer's own key fills those
templates and keeps no vendor knowledge, and the LLM agent fills the same templates on the site's key.
"""
import copy
import ipaddress
import json
import re
from urllib.parse import urlsplit

from prompits.core.pulse_mapping import path_value

IMAGE_TYPES = ('image/png', 'image/jpeg', 'image/webp', 'image/gif')
MAX_PARTS = 20
MAX_IMAGES = 2
MAX_IMAGE_CHARS = 1_000_000  # base64 characters of one picture (a Terminal's form sends at most 900,000)
MAX_URL = 2000
BASE64 = re.compile(r'[A-Za-z0-9+/]+={0,2}')
TEMPLATE_KINDS = ('text', 'imageData', 'imageUrl')


def https_url(value):
    """The URL when it is a public https address by host name; else ValueError."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL or any(c.isspace() for c in value):
        raise ValueError('A link is one https address of at most 2000 characters.')
    parts = urlsplit(value)
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or not host or parts.username or parts.password:
        raise ValueError('A link must start with https:// and name a web site.')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError('A link must name a web site, not a numeric address.')
    if '.' not in host or host.endswith(('.local', '.localhost', '.internal')) or host == 'localhost':
        raise ValueError('A link must name a public web site.')
    return value


def checked_part(part):
    if not isinstance(part, dict) or part.get('type') not in ('text', 'image'):
        raise ValueError('A message part is text or an image.')
    if part['type'] == 'text':
        if set(part) != {'type', 'text'} or not isinstance(part['text'], str) or not part['text'].strip():
            raise ValueError('A text part has non-empty text.')
        return 'text'
    if set(part) == {'type', 'url'}:
        https_url(part['url'])
        return 'image'
    if set(part) != {'type', 'mediaType', 'data'} or part['mediaType'] not in IMAGE_TYPES:
        raise ValueError('An image part is a PNG, JPEG, WebP or GIF picture, or an https link to one.')
    data = part['data']
    if not isinstance(data, str) or not 0 < len(data) <= MAX_IMAGE_CHARS or not BASE64.fullmatch(data):
        raise ValueError(f'An image part carries base64 data of at most {MAX_IMAGE_CHARS} characters.')
    return 'image'


def check_messages(messages, *, roles=('system', 'user', 'assistant'), max_text_bytes=100_000, max_messages=16):
    """Bounded, well-formed messages; returns (text bytes, picture count). Pictures count apart from text."""
    if not isinstance(messages, list) or not 1 <= len(messages) <= max_messages:
        raise ValueError(f'Send 1 to {max_messages} messages.')
    images, text = 0, []
    for message in messages:
        if not isinstance(message, dict) or set(message) != {'role', 'content'} or message['role'] not in roles:
            raise ValueError('A message has a role and content.')
        content = message['content']
        if isinstance(content, str):
            if not content.strip():
                raise ValueError('A message has non-empty content.')
            text.append(content)
            continue
        if message['role'] == 'system' or not isinstance(content, list) or not 1 <= len(content) <= MAX_PARTS:
            raise ValueError(f'A user or assistant message has text or a list of 1 to {MAX_PARTS} parts.')
        for part in content:
            if checked_part(part) == 'image':
                images += 1
            else:
                text.append(part['text'])
    if images > MAX_IMAGES:
        raise ValueError(f'Send at most {MAX_IMAGES} pictures.')
    size = len(json.dumps(text, ensure_ascii=False).encode())
    if size > max_text_bytes:
        raise ValueError(f'The text of the messages exceeds {max_text_bytes // 1000} KB.')
    return size, images


def has_parts(messages):
    return any(isinstance(m.get('content'), list) for m in messages if isinstance(m, dict))


def content_text(content):
    """The text a message carries, for size limits and for providers that read text only."""
    if isinstance(content, str):
        return content
    return '\n'.join(p['text'] for p in content if isinstance(p, dict) and p.get('type') == 'text')


def fill(template, values):
    """A deep copy of `template` with every "$name" in its strings replaced by values[name]; a string that is
    exactly one placeholder takes the value itself."""
    if isinstance(template, str):
        if template.startswith('$') and template[1:] in values:
            return values[template[1:]]
        return re.sub(r'\$([A-Za-z]+)', lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)
    if isinstance(template, list):
        return [fill(item, values) for item in template]
    if isinstance(template, dict):
        return {key: fill(item, values) for key, item in template.items()}
    return copy.deepcopy(template)


def checked_templates(parts):
    """The endpoint's part templates; ValueError when it publishes none a request with pictures needs."""
    if not isinstance(parts, dict) or any(kind not in parts or not isinstance(parts[kind], dict) for kind in TEMPLATE_KINDS):
        raise ValueError('This LLM endpoint does not take pictures.')
    return parts


def wire_content(content, parts):
    """A message's content as the endpoint writes it: text stays text; a list of parts fills the part templates."""
    if isinstance(content, str):
        return content
    templates = checked_templates(parts)
    written = []
    for part in content:
        if part['type'] == 'text':
            written.append(fill(templates['text'], {'text': part['text']}))
        elif 'url' in part:
            written.append(fill(templates['imageUrl'], {'url': part['url']}))
        else:
            written.append(fill(templates['imageData'], {'mediaType': part['mediaType'], 'data': part['data']}))
    return written


def web_fetch_tool(spec, model, url):
    """(body field, value) that lets `model` read the one page at `url`, from the endpoint's published variants."""
    https_url(url)
    host = urlsplit(url).hostname.lower()
    for variant in spec if isinstance(spec, list) else []:
        if isinstance(variant, dict) and model in (variant.get('models') or []) and isinstance(variant.get('field'), str):
            return variant['field'], fill(variant.get('value'), {'host': host, 'url': url})
    raise ValueError('This model cannot read a link.')


def answer_text(data, spec, fallback_path=None):
    """The generated text of a reply. `spec` joins the matching blocks of a list after the last block a tool
    wrote ({"list", "match", "field", "after"}), so a model's note before it read a page is left out."""
    if isinstance(spec, dict) and isinstance(spec.get('list'), str):
        blocks = path_value(data, spec['list'])
        if isinstance(blocks, list):
            after = spec.get('after') if isinstance(spec.get('after'), dict) else None
            start = 0
            if after:
                hits = [i for i, b in enumerate(blocks) if isinstance(b, dict) and all(b.get(k) == v for k, v in after.items())]
                start = hits[-1] + 1 if hits else 0
            match = spec.get('match') if isinstance(spec.get('match'), dict) else {}
            field = spec.get('field') if isinstance(spec.get('field'), str) else 'text'
            texts = [b.get(field) for b in blocks[start:] if isinstance(b, dict) and all(b.get(k) == v for k, v in match.items())]
            return ''.join(t for t in texts if isinstance(t, str))
    return path_value(data, fallback_path) if isinstance(fallback_path, str) else None
