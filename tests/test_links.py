import base64
import json
from threading import Event
from unittest import TestCase
from unittest.mock import patch

from drift import links
from drift.store import Track


class LinkTests(TestCase):
    key = 'a' * 22
    item = {'__typename': 'Track', 'name': 'A song', 'firstArtist': {
        'items': [{'profile': {'name': 'An artist'}}]}, 'duration': {'totalMilliseconds': 200_000}}

    def test_spotify_track_uses_artist_and_version_to_match(self):
        remix = Track('1', 'A song (Remix)', 'An artist', duration=200_000)
        wrong_artist = Track('2', 'A song', 'Someone else', duration=200_000)
        right = Track('3', 'A song', 'An artist', duration=200_500)
        with patch('drift.links.online.search', return_value=[remix, wrong_artist, right]):
            self.assertEqual(links._match(self.item, False), right)

    def test_playlist_reports_partial_catalog_and_keeps_repeats(self):
        state = {'entities': {'items': {f'spotify:playlist:{self.key}': {'content': {
            'totalCount': 4, 'items': [{'itemV2': {'data': self.item}}] * 2}}}}}
        payload = base64.b64encode(json.dumps(state).encode())
        page = b'<script id="initialState" type="text/plain">' + payload + b'</script>'
        track = Track('1', 'A song', 'An artist', duration=200_000)
        found = []
        with patch('drift.links.online.fetch', return_value=page), patch('drift.links._match', return_value=track):
            missing = links.tracks_from_link(f'https://open.spotify.com/playlist/{self.key}', Event(), found.append)
        self.assertEqual(found, [track, track])
        self.assertEqual(missing, 2)
