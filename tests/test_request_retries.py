"""common.request_with_retries: a request that fails in a way that usually passes is asked for again."""

import httpx
import pytest

from error_scrapers import common


def _flaky_client(failures, status=200, error=httpx.ReadTimeout):
    """A client whose requests raise `error` the first `failures` times."""
    calls = []

    def handler(request):
        calls.append(request.method)
        if len(calls) <= failures:
            raise error("The read operation timed out", request=request)
        return httpx.Response(status, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.calls = calls
    return client


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(common, "PAGE_RETRY_SECONDS", 0)


def test_a_request_that_times_out_once_is_made_again():
    client = _flaky_client(failures=1)

    response = common.request_with_retries(client, "get", "https://example.test/page")

    assert response.text == "ok"
    assert len(client.calls) == 2


def test_it_gives_up_after_three_attempts_and_raises_the_timeout():
    client = _flaky_client(failures=99)

    with pytest.raises(httpx.ReadTimeout):
        common.request_with_retries(client, "get", "https://example.test/page")

    assert len(client.calls) == common.PAGE_ATTEMPTS == 3


def test_a_post_is_retried_the_same_way():
    client = _flaky_client(failures=2)

    response = common.request_with_retries(
        client, "post", "https://example.test/api", json={"page": 1})

    assert response.status_code == 200
    assert client.calls == ["POST", "POST", "POST"]


def test_every_kind_of_timeout_is_retried():
    for error in (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout):
        client = _flaky_client(failures=1, error=error)
        assert common.request_with_retries(client, "get", "https://example.test/").text == "ok"


@pytest.mark.parametrize("status", [403, 404, 429])
def test_a_status_that_means_stop_or_missing_is_returned_not_retried(status):
    # A 403/429 is the portal telling us to stop; the caller decides what to do.
    client = _flaky_client(failures=0, status=status)

    response = common.request_with_retries(client, "get", "https://example.test/page")

    assert response.status_code == status
    assert len(client.calls) == 1


def _status_client(statuses, headers=None):
    """A client answering with these statuses in turn (the last one repeats)."""
    calls = []

    def handler(request):
        calls.append(request.method)
        status = statuses[min(len(calls), len(statuses)) - 1]
        return httpx.Response(status, text="ok" if status == 200 else "error", headers=headers or {})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.calls = calls
    return client


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_server_error_is_asked_for_again(status):
    client = _status_client([status, 200])

    response = common.request_with_retries(client, "get", "https://example.test/page")

    assert response.status_code == 200
    assert len(client.calls) == 2


def test_a_server_error_that_keeps_coming_is_returned_after_three_attempts():
    client = _status_client([503])

    response = common.request_with_retries(client, "get", "https://example.test/page")

    assert response.status_code == 503      # the caller's raise_for_status sees it
    assert len(client.calls) == common.PAGE_ATTEMPTS == 3


@pytest.mark.parametrize("headers", [
    {"cf-mitigated": "challenge"},     # Cloudflare's challenge page
    {"x-amzn-waf-action": "challenge"},  # AWS WAF
])
def test_a_block_or_challenge_is_never_retried_even_when_it_is_a_5xx(headers):
    client = _status_client([503], headers=headers)

    response = common.request_with_retries(client, "get", "https://example.test/page")

    assert response.status_code == 503
    assert len(client.calls) == 1


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError])
def test_a_dropped_or_refused_connection_is_asked_for_again(error):
    client = _flaky_client(failures=1, error=error)

    assert common.request_with_retries(client, "get", "https://example.test/page").text == "ok"
    assert len(client.calls) == 2


def test_a_connection_that_never_comes_back_raises_after_three_attempts():
    client = _flaky_client(failures=99, error=httpx.ConnectError)

    with pytest.raises(httpx.ConnectError):
        common.request_with_retries(client, "get", "https://example.test/page")

    assert len(client.calls) == common.PAGE_ATTEMPTS
