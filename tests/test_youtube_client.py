import json
from unittest import mock

import pytest

from src.youtube_client import YouTubeClient, YouTubeApiError


class TestYouTubeClientInit:
    def test_init_with_api_key(self):
        client = YouTubeClient(api_key="test_key")
        assert client.api_key == "test_key"

    def test_init_with_access_token(self):
        client = YouTubeClient(access_token="token123")
        assert client.access_token == "token123"

    def test_init_from_env(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "env_key")
        client = YouTubeClient()
        assert client.api_key == "env_key"

    def test_init_raises_without_credentials(self, monkeypatch):
        # Clear any existing env variables
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        monkeypatch.delenv("YOUTUBE_ACCESS_TOKEN", raising=False)
        with pytest.raises(ValueError):
            YouTubeClient()

    def test_init_custom_timeout_and_retry(self):
        client = YouTubeClient(api_key="key", timeout=10.5, retry_max=5)
        assert client.timeout == 10.5
        assert client.retry_max == 5


class TestYouTubeClientHeaders:
    def test_headers_with_api_key(self):
        client = YouTubeClient(api_key="key123")
        headers = client._headers()
        assert headers["Accept"] == "application/json"
        assert "Authorization" not in headers

    def test_headers_with_access_token(self):
        client = YouTubeClient(access_token="tok123")
        headers = client._headers()
        assert headers["Authorization"] == "Bearer tok123"


class TestYouTubeClientBuildUrl:
    def test_build_url_with_api_key(self):
        client = YouTubeClient(api_key="abc123")
        url = client._build_url("channels", {"id": "ch1", "part": "snippet"})
        assert "https://www.googleapis.com/youtube/v3/channels" in url
        assert "key=abc123" in url
        assert "id=ch1" in url
        assert "part=snippet" in url

    def test_build_url_filters_none_values(self):
        client = YouTubeClient(api_key="key")
        url = client._build_url("test", {"a": "value", "b": None, "c": "other"})
        assert "b=" not in url
        assert "a=value" in url
        assert "c=other" in url

    def test_build_url_custom_base(self):
        client = YouTubeClient(api_key="key", base_url="http://custom/api")
        url = client._build_url("channels", {"id": "ch"})
        assert url.startswith("http://custom/api/channels?")


class TestYouTubeClientRequest:
    @mock.patch("urllib.request.urlopen")
    def test_successful_request(self, mock_urlopen):
        response_data = {"items": [{"id": "ch1"}]}
        mock_response = mock.Mock()
        mock_response.read.return_value = json.dumps(response_data).encode("utf-8")
        mock_response.__enter__ = mock.Mock(return_value=mock_response)
        mock_response.__exit__ = mock.Mock(return_value=None)
        mock_urlopen.return_value = mock_response

        client = YouTubeClient(api_key="key")
        result = client.request("channels", {"id": "ch1"})
        assert result == response_data

    @mock.patch("urllib.request.urlopen")
    def test_request_with_empty_response(self, mock_urlopen):
        mock_response = mock.Mock()
        mock_response.read.return_value = b""
        mock_response.__enter__ = mock.Mock(return_value=mock_response)
        mock_response.__exit__ = mock.Mock(return_value=None)
        mock_urlopen.return_value = mock_response

        client = YouTubeClient(api_key="key")
        result = client.request("channels", {})
        assert result == {}

    @mock.patch("urllib.request.urlopen")
    def test_http_error_non_retryable(self, mock_urlopen):
        import urllib.error
        http_error = urllib.error.HTTPError("url", 400, "Bad request", {}, None)
        mock_urlopen.side_effect = http_error

        client = YouTubeClient(api_key="key")
        with pytest.raises(YouTubeApiError) as exc_info:
            client.request("channels", {})
        assert exc_info.value.status == 400

    @mock.patch("time.sleep")
    @mock.patch("urllib.request.urlopen")
    def test_retry_on_500(self, mock_urlopen, mock_sleep):
        import urllib.error
        http_error = urllib.error.HTTPError("url", 500, "Server error", {}, None)
        response_data = {"items": []}

        mock_response = mock.Mock()
        mock_response.read.return_value = json.dumps(response_data).encode("utf-8")
        mock_response.__enter__ = mock.Mock(return_value=mock_response)
        mock_response.__exit__ = mock.Mock(return_value=None)

        mock_urlopen.side_effect = [http_error, mock_response]

        client = YouTubeClient(api_key="key", retry_max=1)
        result = client.request("channels", {})
        assert result == response_data
        assert mock_sleep.called

    @mock.patch("time.sleep")
    @mock.patch("urllib.request.urlopen")
    def test_retry_on_429(self, mock_urlopen, mock_sleep):
        import urllib.error
        http_error = urllib.error.HTTPError("url", 429, "Quota exceeded", {}, None)
        response_data = {"items": []}

        mock_response = mock.Mock()
        mock_response.read.return_value = json.dumps(response_data).encode("utf-8")
        mock_response.__enter__ = mock.Mock(return_value=mock_response)
        mock_response.__exit__ = mock.Mock(return_value=None)

        mock_urlopen.side_effect = [http_error, mock_response]

        client = YouTubeClient(api_key="key", retry_max=1)
        result = client.request("channels", {})
        assert result == response_data
        mock_sleep.assert_called()

    @mock.patch("time.sleep")
    @mock.patch("urllib.request.urlopen")
    def test_url_error_retry(self, mock_urlopen, mock_sleep):
        import urllib.error
        url_error = urllib.error.URLError("Connection refused")
        response_data = {"items": []}

        mock_response = mock.Mock()
        mock_response.read.return_value = json.dumps(response_data).encode("utf-8")
        mock_response.__enter__ = mock.Mock(return_value=mock_response)
        mock_response.__exit__ = mock.Mock(return_value=None)

        mock_urlopen.side_effect = [url_error, mock_response]

        client = YouTubeClient(api_key="key", retry_max=1)
        result = client.request("channels", {})
        assert result == response_data


class TestYouTubeClientList:
    @mock.patch("urllib.request.urlopen")
    def test_list_method(self, mock_urlopen):
        response_data = {"items": []}
        mock_response = mock.Mock()
        mock_response.read.return_value = json.dumps(response_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        client = YouTubeClient(api_key="key")
        result = client.list("channels", ["snippet", "statistics"], id="ch1")
        assert result == response_data
        # Verify 'part' was properly formatted
        call_args = mock_urlopen.call_args
        assert call_args is not None

    @mock.patch("urllib.request.urlopen")
    def test_list_with_fields(self, mock_urlopen):
        response_data = {"items": []}
        mock_response = mock.Mock()
        mock_response.read.return_value = json.dumps(response_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        client = YouTubeClient(api_key="key")
        result = client.list(
            "channels",
            ["snippet"],
            fields="id,snippet(title)",
            id="ch1",
        )
        assert result == response_data


class TestYouTubeClientTracker:
    @mock.patch("urllib.request.urlopen")
    def test_request_with_tracker(self, mock_urlopen):
        response_data = {}
        mock_response = mock.Mock()
        mock_response.read.return_value = json.dumps(response_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        tracker = mock.Mock()
        client = YouTubeClient(api_key="key", tracker=tracker)
        client.list("channels", ["snippet"], id="ch1")

        tracker.on_request.assert_called_once()
        args = tracker.on_request.call_args
        assert args[0][0] == "channels"  # resource
        assert "id" in args[0][1]  # params dict

    @mock.patch("urllib.request.urlopen")
    def test_tracker_exception_ignored(self, mock_urlopen):
        response_data = {}
        mock_response = mock.Mock()
        mock_response.read.return_value = json.dumps(response_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        tracker = mock.Mock()
        tracker.on_request.side_effect = Exception("Tracker error")
        client = YouTubeClient(api_key="key", tracker=tracker)
        # Should not raise despite tracker error
        result = client.list("channels", ["snippet"], id="ch1")
        assert result == response_data
