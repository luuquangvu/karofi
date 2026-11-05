"""Regression tests for the Home Assistant compatibility resolver."""

from collections.abc import Mapping

import pytest

from tools import validate_compatibility


def _json_text(payload: object) -> str:
    """Serialize a mock PyPI payload as JSON text."""
    return validate_compatibility.orjson.dumps(payload).decode()


def _lagging_harness_payload(
    root_ha_version: str,
    root_version: str = "0.13.376",
) -> str:
    """Return harness metadata whose root info lags behind its newest release."""
    return _json_text(
        {
            "info": {
                "version": root_version,
                "requires_dist": [f"homeassistant=={root_ha_version}"],
            },
            "releases": {
                "0.13.375": [{"filename": "older-harness.whl"}],
                "0.13.376": [{"filename": "newer-harness.whl"}],
            },
        }
    )


def _harness_version_url(version: str) -> str:
    """Return the mock PyPI URL for one legacy harness release."""
    return validate_compatibility._PYPI_TEST_HARNESS_VERSION_JSON_URL_TEMPLATE.format(
        version=version
    )


def _mock_remote_responses(
    monkeypatch: pytest.MonkeyPatch,
    responses: Mapping[str, str | Exception],
) -> None:
    """Install a fake remote fetcher backed by a URL-to-response mapping."""

    def fake_fetch(url: str) -> str:
        response = responses.get(url)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, str):
            return response
        raise AssertionError(f"Unexpected URL fetched: {url}")

    monkeypatch.setattr(validate_compatibility, "_fetch_remote_text", fake_fetch)


def test_matched_pair_prefers_newest_matching_ha_over_newest_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefer the greatest matching Home Assistant version within the candidate window."""
    _mock_remote_responses(
        monkeypatch,
        {
            validate_compatibility._PYPI_TEST_HARNESS_JSON_URL: _lagging_harness_payload(
                "2026.9.0"
            ),
            _harness_version_url("0.13.375"): _json_text(
                {
                    "info": {
                        "version": "0.13.375",
                        "requires_dist": ["homeassistant==2026.10.0"],
                    }
                }
            ),
        },
    )

    assert validate_compatibility._matched_pair_for_package(
        validate_compatibility._LEGACY_TEST_HARNESS_PACKAGE,
        {
            "2026.9.0": [{"filename": "old-ha.whl"}],
            "2026.10.0": [{"filename": "new-ha.whl"}],
        },
    ) == ("2026.10.0", "0.13.375")


def test_matched_pair_stops_after_newest_ha_for_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop scanning once the newest Home Assistant release for the package is matched."""
    ha_releases: dict[str, object] = {"2026.10.4": [{"filename": "ha.whl"}]}
    _mock_remote_responses(
        monkeypatch,
        {
            validate_compatibility._PYPI_TEST_HARNESS_JSON_URL: _lagging_harness_payload(
                "2026.10.4"
            ),
        },
    )

    assert validate_compatibility._matched_pair_for_package(
        validate_compatibility._LEGACY_TEST_HARNESS_PACKAGE,
        ha_releases,
    ) == ("2026.10.4", "0.13.376")


def test_matched_pair_ignores_failed_lookup_for_later_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unavailable older candidate must not invalidate an already matched pair."""
    ha_releases: dict[str, object] = {
        "2026.9.0": [{"filename": "old-ha.whl"}],
        "2026.10.0": [{"filename": "new-ha.whl"}],
    }
    _mock_remote_responses(
        monkeypatch,
        {
            validate_compatibility._PYPI_TEST_HARNESS_JSON_URL: _lagging_harness_payload(
                "2026.9.0"
            ),
            _harness_version_url("0.13.375"): OSError("transient PyPI failure"),
        },
    )

    assert validate_compatibility._matched_pair_for_package(
        validate_compatibility._LEGACY_TEST_HARNESS_PACKAGE,
        ha_releases,
    ) == ("2026.9.0", "0.13.376")


def test_matched_pair_propagates_candidate_error_before_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not fall back when the newest candidate metadata cannot be fetched."""
    _mock_remote_responses(
        monkeypatch,
        {
            validate_compatibility._PYPI_TEST_HARNESS_JSON_URL: _lagging_harness_payload(
                "2026.9.0",
                root_version="0.13.375",
            ),
            _harness_version_url("0.13.376"): OSError("transient PyPI failure"),
        },
    )

    with pytest.raises(ValueError, match="transient PyPI failure"):
        validate_compatibility._matched_pair_for_package(
            validate_compatibility._LEGACY_TEST_HARNESS_PACKAGE,
            {"2026.9.0": [{"filename": "ha.whl"}]},
        )


def test_latest_matched_pair_surfaces_unusable_harness_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface harness metadata errors instead of reporting publication lag."""
    _mock_remote_responses(
        monkeypatch,
        {
            validate_compatibility._PYPI_HA_JSON_URL: _json_text(
                {"releases": {"2026.10.4": [{"filename": "ha.whl"}]}}
            ),
            validate_compatibility._PYPI_TEST_HARNESS_JSON_URL: _json_text(
                {
                    "info": {
                        "version": "0.13.375",
                        "requires_dist": ["homeassistant>=2026.10.4"],
                    },
                    "releases": {"0.13.375": [{"filename": "harness.whl"}]},
                }
            ),
            validate_compatibility._PYPI_NEW_TEST_HARNESS_JSON_URL: _json_text(
                {"info": {"version": "0.1.3"}, "releases": {}}
            ),
        },
    )

    with pytest.raises(
        ValueError,
        match=(
            r"Failed to resolve latest matched harness-backed Home Assistant pair: "
            r".*must pin Home Assistant with one exact version"
        ),
    ):
        validate_compatibility._get_latest_matched_pair()
