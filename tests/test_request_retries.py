"""common.request_with_retries: a request that times out is asked for again."""

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


@pytest.mark.parametrize("status", [403, 429, 500])
def test_an_http_status_is_returned_not_retried(status):
    # A 403/429 is the portal telling us to stop; the caller decides what to do.
    client = _flaky_client(failures=0, status=status)

    response = common.request_with_retries(client, "get", "https://example.test/page")

    assert response.status_code == status
    assert len(client.calls) == 1


def test_a_refused_connection_is_not_retried():
    client = _flaky_client(failures=99, error=httpx.ConnectError)

    with pytest.raises(httpx.ConnectError):
        common.request_with_retries(client, "get", "https://example.test/page")

    assert len(client.calls) == 1


def test_each_retry_waits_longer(monkeypatch):
    monkeypatch.setattr(common, "PAGE_RETRY_SECONDS", 5)
    waits = []
    monkeypatch.setattr(common.time, "sleep", waits.append)
    client = _flaky_client(failures=2)

    common.request_with_retries(client, "get", "https://example.test/page")

    assert waits == [5, 10]


def test_it_does_not_wait_after_the_last_attempt(monkeypatch):
    monkeypatch.setattr(common, "PAGE_RETRY_SECONDS", 5)
    waits = []
    monkeypatch.setattr(common.time, "sleep", waits.append)
    client = _flaky_client(failures=99)

    with pytest.raises(httpx.ReadTimeout):
        common.request_with_retries(client, "get", "https://example.test/page")

    assert waits == [5, 10]  # after attempts 1 and 2 only
