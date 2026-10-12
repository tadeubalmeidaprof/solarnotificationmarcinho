import unittest
from unittest.mock import Mock, patch

import maintenance_report
import solar_queries
from solis_location import (
    SolisLocationError,
    coordinates_from_station,
    resolve_coordinates,
)
from solisclient import SolisAPIError


LOCATION = {"latitude": "-14.25", "longitude": "-42.25"}


class SolisLocationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict("os.environ", {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_manual_coordinates_override_api_without_request(self):
        loader = Mock()
        with patch.dict("os.environ", {
            "STATION_LATITUDE": "-14,25",
            "STATION_LONGITUDE": "-42,25",
        }):
            self.assertEqual(resolve_coordinates({}, loader), (-14.25, -42.25))
        loader.assert_not_called()

    def test_list_coordinates_avoid_detail_request(self):
        loader = Mock()
        self.assertEqual(resolve_coordinates(LOCATION, loader), (-14.25, -42.25))
        loader.assert_not_called()

    def test_missing_list_location_uses_station_detail(self):
        loader = Mock(return_value={"data": LOCATION})
        self.assertEqual(resolve_coordinates({"id": 1}, loader), (-14.25, -42.25))
        loader.assert_called_once_with()

    def test_invalid_list_location_uses_valid_detail_pair(self):
        loader = Mock(return_value={"data": LOCATION})
        self.assertEqual(
            resolve_coordinates({"latitude": "nan", "longitude": 200}, loader),
            (-14.25, -42.25),
        )

    def test_partial_manual_override_does_not_mix_sources(self):
        loader = Mock(return_value={"data": LOCATION})
        with patch.dict("os.environ", {"STATION_LATITUDE": "-14.25"}):
            with self.assertRaises(SolisLocationError):
                resolve_coordinates(LOCATION, loader)
        loader.assert_not_called()

    def test_invalid_manual_override_is_rejected(self):
        with patch.dict("os.environ", {
            "STATION_LATITUDE": "91",
            "STATION_LONGITUDE": "-42.25",
        }):
            with self.assertRaises(SolisLocationError):
                resolve_coordinates(LOCATION)

    def test_missing_or_malformed_detail_is_actionable(self):
        for response in ({"data": {}}, {"data": None}, {"data": []}, None):
            with self.subTest(response=response):
                with self.assertRaisesRegex(SolisLocationError, "SolisCloud"):
                    resolve_coordinates({}, Mock(return_value=response))

    def test_api_error_is_wrapped(self):
        with self.assertRaisesRegex(SolisLocationError, "stationDetail"):
            resolve_coordinates({}, Mock(side_effect=SolisAPIError("unavailable")))

    def test_coordinates_reject_nonfinite_and_out_of_range_values(self):
        for pair in (("nan", 0), (0, "inf"), (-91, 0), (0, -181), (None, 0)):
            with self.subTest(pair=pair):
                self.assertIsNone(coordinates_from_station({
                    "latitude": pair[0], "longitude": pair[1],
                }))
        self.assertEqual(
            coordinates_from_station({"latitude": 0, "longitude": 0}),
            (0.0, 0.0),
        )

    def test_bot_weather_uses_detail_when_list_omits_location(self):
        client = Mock()
        client.list_stations.return_value = {"data": {"page": {
            "records": [{"id": "station-1"}],
        }}}
        client.station_detail.return_value = {"data": LOCATION}
        with patch("solar_queries._client", return_value=client), patch(
            "solar_queries._station_id", return_value="station-1",
        ):
            self.assertEqual(solar_queries._coordinates(), (-14.25, -42.25))
        client.station_detail.assert_called_once_with()

    def test_bot_location_failure_returns_unavailable(self):
        client = Mock()
        client.list_stations.side_effect = SolisAPIError("unavailable")
        client.station_detail.side_effect = SolisAPIError("unavailable")
        with patch("solar_queries._client", return_value=client):
            self.assertIsNone(solar_queries._coordinates())

    def test_preventive_monitor_saves_weather_using_detail_location(self):
        client = Mock()
        client.list_stations.return_value = {"data": {"page": {
            "records": [{"id": "station-1", "dayEnergy": 10, "monthEnergy": 50}],
        }}}
        client.station_detail.return_value = {"data": LOCATION}
        credentials = {
            "SOLIS_API_ID": "test", "SOLIS_API_SECRET": "test",
            "SOLIS_BASE_URL": "https://example.invalid",
            "SOLIS_STATION_ID": "station-1",
        }
        weather = {"CLASSIFICACAOCLIMA": "favorable"}
        with patch("maintenance_report.load_environment", return_value=credentials), \
            patch("maintenance_report.SolisClient", return_value=client), \
            patch("maintenance_report.save_daily_generation"), \
            patch("maintenance_report.get_daily_weather", return_value=weather) as get_weather, \
            patch("maintenance_report.save_daily_weather") as save_weather, \
            patch("maintenance_report.expire_stale_pending_maintenance_alerts"), \
            patch("maintenance_report.fetch_monitoring_history", return_value=[]), \
            patch("maintenance_report.send_message") as send:
            maintenance_report.main()
        client.station_detail.assert_called_once_with()
        self.assertEqual(get_weather.call_args.kwargs["latitude"], -14.25)
        self.assertEqual(get_weather.call_args.kwargs["longitude"], -42.25)
        self.assertEqual(save_weather.call_args.kwargs["weather"], weather)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
