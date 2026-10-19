"""Small injectable HTTP layer. Never expose upstream URLs or response bodies in errors."""
import time
from typing import Any
import httpx


class ExternalError(RuntimeError):
    pass


class JsonHTTP:
    def __init__(self, client: httpx.Client | None = None, retries: int = 2, sleep=time.sleep):
        self.client = client or httpx.Client(timeout=120, follow_redirects=False)
        self.retries = retries
        self.sleep = sleep

    def request(self, method: str, url: str, *, business=False, before_attempt=None, **kwargs) -> Any:
        for attempt in range(self.retries + 1):
            if before_attempt:
                before_attempt()
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TimeoutException:
                # A timed-out paid request may already have succeeded; do not replay.
                raise ExternalError('upstream_timeout_no_retry') from None
            except httpx.RequestError:
                raise ExternalError('upstream_connection_error') from None
            try:
                data = response.json()
            except ValueError:
                data = None
            code = data.get('code') if business and isinstance(data, dict) else None
            permanent = business and code in (100, 303, 400, 600, 601, 602)
            retryable = not permanent and (response.status_code in (429, 500, 502, 503, 504) or (business and code in (301, 302, 500)))
            if retryable and attempt < self.retries:
                self.sleep(min(2 ** attempt, 8))
                continue
            if business and code is not None and code != 0:
                raise ExternalError(f'upstream_business_code_{code}' if isinstance(code, int) else 'upstream_invalid_code')
            if not 200 <= response.status_code < 300:
                raise ExternalError(f'upstream_http_{response.status_code}')
            if not isinstance(data, dict):
                raise ExternalError('upstream_invalid_json_object')
            if business and (code != 0 or 'data' not in data):
                raise ExternalError('upstream_invalid_envelope')
            return data
        raise ExternalError('upstream_retry_exhausted')

    def close(self):
        self.client.close()


class JustOneAPI:
    def __init__(self, token: str, http: JsonHTTP, max_calls: int = 100):
        if not token:
            raise ValueError('JUSTONEAPI_TOKEN is required')
        self.token, self.http, self.max_calls = token, http, max_calls
        self.calls = 0

    def _charge(self):
        if self.calls >= self.max_calls:
            raise ExternalError('api_call_budget_exhausted')
        self.calls += 1

    def get(self, endpoint: str, params: dict):
        return self.http.request('GET', 'https://api.justoneapi.com' + endpoint,
                                 params={**params, 'token': self.token}, business=True,
                                 before_attempt=self._charge)['data']
