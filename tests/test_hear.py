import io
import json
import urllib.error
from unittest import TestCase
from unittest.mock import patch

from drift import hear
from drift.store import Track

ANSWER = {'matches': [{'id': '1'}], 'track': {
    'title': 'Despacito', 'subtitle': 'Luis Fonsi & Daddy Yankee',
    'images': {'coverart': 'https://img/400.jpg', 'coverarthq': 'https://img/800.jpg'},
    'sections': [{'type': 'SONG', 'metadata': [{'title': 'Album', 'text': 'VIDA'}, {'title': 'Released', 'text': '2017'}]}]}}


class HearTests(TestCase):
    def test_an_answer_becomes_title_artist_album_year_cover(self):
        self.assertEqual(hear.about(ANSWER), {'title': 'Despacito', 'artist': 'Luis Fonsi & Daddy Yankee',
                                              'album': 'VIDA', 'year': '2017', 'cover': 'https://img/800.jpg',
                                              'key': '', 'offset': 0.0, 'timeskew': 0.0, 'freqskew': 0.0,
                                              'matches': 1})

    def test_no_match_is_none(self):
        self.assertIsNone(hear.about({'matches': [], 'track': ANSWER['track']}))
        self.assertIsNone(hear.about({}))

    def test_only_the_fingerprint_is_sent(self):
        sent = {}

        def fake(req, timeout):
            sent['url'], sent['body'] = req.full_url, json.loads(req.data)
            return io.BytesIO(json.dumps(ANSWER).encode())

        with patch('drift.hear.urllib.request.urlopen', fake):
            self.assertEqual(hear.recognize('data:audio/vnd.shazam.sig;base64,AAAA', 4000, 123)['title'], 'Despacito')
        self.assertTrue(sent['url'].startswith('https://amp.shazam.com/discovery/v5/'))
        self.assertEqual(sent['body']['signature'], {'samplems': 4000, 'timestamp': 123,
                                                     'uri': 'data:audio/vnd.shazam.sig;base64,AAAA'})

    def test_too_many_tries_says_wait(self):
        def busy(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 429, 'Too Many Requests', {}, None)

        with patch('drift.hear.urllib.request.urlopen', busy), self.assertRaises(hear.Busy):
            hear.recognize('data:x', 4000)

    def test_full_song_splits_credits_and_falls_back_to_the_top_result(self):
        seen = []
        with patch('drift.hear.links._match', side_effect=lambda item, prepare: seen.append(item) or None), \
                patch('drift.hear.online.search', return_value=[Track('yt:1', 'Despacito', 'Luis Fonsi')]):
            self.assertEqual(hear.full_song({'title': 'Despacito', 'artist': 'Luis Fonsi & Daddy Yankee'}).uid, 'yt:1')
        self.assertEqual([a['profile']['name'] for a in seen[0]['artists']['items']], ['Luis Fonsi', 'Daddy Yankee'])


def answer(title='Despacito', key='1', offset=100.0, start=0.0):
    return {'title': title, 'artist': 'Luis Fonsi', 'key': key, 'offset': offset, 'from': start}


class AgreeTests(TestCase):
    def test_two_listens_at_the_same_moment_name_it(self):
        self.assertTrue(hear.sure([answer(offset=100.0)], answer(offset=101.2, start=1.0)))

    def test_a_guess_that_jumps_around_the_song_is_not_named_until_it_comes_back(self):
        first, second = answer(offset=140.7), answer(offset=119.1)       # a chorus heard twice
        self.assertFalse(hear.sure([first], second))
        self.assertTrue(hear.sure([first, second], answer(offset=60.0)))  # third time
        self.assertTrue(hear.sure([first], second, final=True))           # twice, at the end

    def test_different_songs_never_agree(self):
        self.assertFalse(hear.sure([answer(key='1')], answer(title='Other', key='2'), final=True))

    def test_full_song_never_swaps_in_a_different_title(self):
        with patch('drift.hear.links._match', return_value=None), patch('drift.hear.online.search', return_value=[Track('yt:2', 'Another Song', 'Luis Fonsi')]):
            self.assertIsNone(hear.full_song({'title': 'Despacito', 'artist': 'Luis Fonsi'}))
