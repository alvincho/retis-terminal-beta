"""JSON responses without a repeated Python walk of JSON-native payloads."""
import json

from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse


class NativeJSONResponse(JSONResponse):
    def render(self, content):
        # The native encoder traverses ordinary dict/list/scalar payloads. Only
        # dates, models and other typed values need FastAPI's Python adapter.
        return json.dumps(content, ensure_ascii=False, allow_nan=False,
                          separators=(',', ':'), default=jsonable_encoder).encode('utf-8')
