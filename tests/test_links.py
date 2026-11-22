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
        about, *songs = found
        self.assertEqual(about['about']['total'], 4)
        self.assertEqual(songs, [track, track])
        self.assertEqual(missing, 2)

    def test_playlist_brings_name_description_and_the_full_embed_list(self):
        state = {'entities': {'items': {f'spotify:playlist:{self.key}': {
            'name': 'Road trip', 'description': 'Windows down &amp; <a href="x">loud</a>',
            'content': {'totalCount': 3, 'items': [{'itemV2': {'data': self.item}}]}}}}}
        share = b'<script id="initialState" type="text/plain">' + base64.b64encode(json.dumps(state).encode()) + b'</script>'
        rows = [{'title': f'Song {n}', 'subtitle': 'Tyler, The Creator,\xa0Someone', 'duration': 1000} for n in range(3)]
        data = {'props': {'pageProps': {'state': {'data': {'entity': {'name': 'Road trip', 'trackList': rows}}}}}}
        embed = b'<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data).encode() + b'</script>'
        seen = []

        def match(item, prepare):
            seen.append(item)
            return Track(item['name'], item['name'], 'x')

        found = []
        with patch('drift.links.online.fetch', side_effect=lambda url, **k: embed if '/embed/' in url else share),                 patch('drift.links._match', side_effect=match):
            missing = links.tracks_from_link(f'https://open.spotify.com/playlist/{self.key}', Event(), found.append)
        about = found[0]['about']
        self.assertEqual((about['name'], about['note'], about['total']), ('Road trip', 'Windows down & loud', 3))
        self.assertEqual([t.title for t in found[1:]], ['Song 0', 'Song 1', 'Song 2'])     # in list order
        self.assertEqual([a['profile']['name'] for a in seen[0]['artists']['items']], ['Tyler, The Creator', 'Someone'])
        self.assertEqual(missing, 0)

    def test_one_song_link_has_no_card(self):
        state = {'entities': {'items': {f'spotify:track:{self.key}': self.item}}}
        page = b'<script id="initialState" type="text/plain">' + base64.b64encode(json.dumps(state).encode()) + b'</script>'
        found = []
        with patch('drift.links.online.fetch', return_value=page),                 patch('drift.links._match', return_value=Track('1', 'A song', 'An artist')):
            links.tracks_from_link(f'https://open.spotify.com/track/{self.key}', Event(), found.append)
        self.assertEqual(len(found), 1)
        self.assertIsInstance(found[0], Track)

    def test_with_credit_in_title_still_matches(self):
        item = {**self.item, 'name': 'A song (with Friend)'}
        right = Track('3', 'A song', 'An artist, Friend', duration=200_000)
        with patch('drift.links.online.search', return_value=[right]):
            self.assertEqual(links._match(item, False), right)


class PlaylistNameTests(TestCase):
    def test_import_gets_a_free_name_and_keeps_its_note_until_deleted(self):
        import tempfile
        from pathlib import Path
        from drift.store import Store
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / 'drift.db')
            store.new_playlist('Road trip')
            name = store.free_playlist_name('road trip')
            self.assertEqual(name, 'road trip 2')
            pid = store.new_playlist(name)
            store.set_playlist_note(pid, 'Windows down')
            self.assertEqual(store.playlist_note(pid), 'Windows down')
            store.drop_playlist(pid)
            self.assertEqual(store.playlist_note(pid), '')
            store.db.close()
