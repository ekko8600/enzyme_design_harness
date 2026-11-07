"""Small standard-library HTTP client for directly executable Skills."""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from typing import Mapping
from urllib.error import HTTPError as UrlHTTPError, URLError
from urllib.request import Request, urlopen


class HttpError(RuntimeError):
    """An HTTP or transport failure with a stable message for Skills."""
    def __init__(self,message,status_code=None):
        super().__init__(message);self.status_code=status_code


@dataclass(frozen=True)
class HttpResponse:
    data: bytes
    headers: Mapping[str, str]
    status: int
    encoding: str = "utf-8"
    final_url: str | None = None

    def json(self):
        return json.loads(self.data.decode(self.encoding))

    @property
    def text(self) -> str:
        return self.data.decode(self.encoding)


def _public_url(url):
    parsed=urlsplit(url)
    query=[(k,'[REDACTED]' if any(x in k.lower() for x in ['key','token','password','secret','email']) else v) for k,v in parse_qsl(parsed.query,keep_blank_values=True)]
    host=parsed.hostname or ''
    if ':' in host:host='['+host+']'
    if parsed.port:host+=':'+str(parsed.port)
    return urlunsplit((parsed.scheme,host,parsed.path,urlencode(query),''))


def _audit(original,final,status,body=None):
    path=os.environ.get('ENZYME_HTTP_AUDIT')
    if not path:return
    value={'original_url':_public_url(original),'final_url':_public_url(final) if final else None,'status':status,'retrieved_at':datetime.now(timezone.utc).isoformat()}
    if body is not None:value.update(size_bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
    with Path(path).open('a',encoding='utf-8') as f:f.write(json.dumps(value,sort_keys=True)+'\n')


class HttpClient:
    def __init__(self, base_url: str, *, qps: float = 1.0, timeout: float = 60.0,
                 referer_skill: str | None = None, default_headers: Mapping[str, str] | None = None):
        self.base_url = base_url.rstrip("/")
        self.qps = max(float(qps), 0.01)
        self.timeout = float(timeout)
        self.referer_skill = referer_skill
        self.default_headers = dict(default_headers or {})
        self._last_request = 0.0

    def _url(self, value: str) -> str:
        return value if value.startswith(("http://", "https://")) else f"{self.base_url}/{value.lstrip('/')}"

    def fetch(self, value: str, *, method: str = "GET", headers: Mapping[str, str] | None = None, data: bytes | str | None = None, max_retries: int = 2, timeout: float | None = None) -> HttpResponse:
        body = data.encode() if isinstance(data, str) else data
        for attempt in range(max(0, int(max_retries)) + 1):
            elapsed = time.monotonic() - self._last_request
            delay = (1.0 / self.qps) - elapsed
            if delay > 0:
                time.sleep(delay)
            request = Request(self._url(value), data=body, method=method.upper(), headers={**self.default_headers, **dict(headers or {})})
            try:
                with urlopen(request, timeout=self.timeout if timeout is None else float(timeout)) as response:
                    self._last_request = time.monotonic()
                    raw_headers = {key: value for key, value in response.headers.items()}
                    encoding = response.headers.get_content_charset() or "utf-8"
                    limit=int(os.environ.get('ENZYME_HTTP_MAX_BYTES',32*1024*1024))
                    if limit<1:raise HttpError('HTTP_MAX_BYTES_INVALID')
                    body=response.read(limit+1);final=response.geturl() if hasattr(response,'geturl') else None
                    if len(body)>limit:
                        _audit(request.full_url,final,'REJECTED_TOO_LARGE');raise HttpError('HTTP_RESPONSE_TOO_LARGE:'+str(limit))
                    _audit(request.full_url,final,int(response.status),body)
                    return HttpResponse(body, raw_headers, int(response.status), encoding,final)
            except UrlHTTPError as exc:
                self._last_request = time.monotonic()
                if exc.code in {429, 500, 502, 503, 504} and attempt < max_retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                _audit(request.full_url,exc.geturl(),exc.code)
                raise HttpError(f"HTTP {exc.code} for {_public_url(self._url(value))}: {detail}",status_code=exc.code) from exc
            except URLError as exc:
                if attempt < max_retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise HttpError(f"network error for {_public_url(self._url(value))}: {exc.reason}") from exc
        raise AssertionError("unreachable")

    def fetch_json(self, value: str, *, method: str = "GET",
                   headers: Mapping[str, str] | None = None,
                   data: bytes | str | None = None,
                   max_retries: int = 2,
                   timeout: float | None = None):
        """Fetch and decode a JSON response for legacy Skill wrappers.

        Keep decoding here so directly executable Skills share the same rate
        limiting, retry, HTTP error and timeout behavior as ``fetch``.
        """
        return self.fetch(value, method=method, headers=headers, data=data,
                          max_retries=max_retries, timeout=timeout).json()

    def fetch_bytes(self, value: str, *, method: str = "GET",
                    headers: Mapping[str, str] | None = None,
                    data: bytes | str | None = None,
                    max_retries: int = 2,
                    timeout: float | None = None) -> bytes:
        """Fetch raw bytes for structure, PDF and archive Skill wrappers."""
        return self.fetch(value, method=method, headers=headers, data=data,
                          max_retries=max_retries, timeout=timeout).data

    def fetch_text(self, value: str, *, method: str = "GET",
                   headers: Mapping[str, str] | None = None,
                   data: bytes | str | None = None,
                   max_retries: int = 2,
                   timeout: float | None = None) -> str:
        """Fetch and decode a text response for legacy Skill wrappers."""
        return self.fetch(value, method=method, headers=headers, data=data,
                          max_retries=max_retries, timeout=timeout).text
