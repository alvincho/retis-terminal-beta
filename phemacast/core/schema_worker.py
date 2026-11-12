"""Disposable validator; deliberately independent of application imports."""
import json
import sys
try:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    if sys.platform.startswith('linux'):
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
except (ImportError, ValueError, OSError):
    pass
from jsonschema import Draft202012Validator, FormatChecker
try:
    data = json.loads(sys.stdin.buffer.read(4_300_001))
    Draft202012Validator(data['schema'], format_checker=FormatChecker()).validate(data['value'])
except Exception:
    sys.exit(1)
sys.stdout.buffer.write(b'valid')
