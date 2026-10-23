import unittest
import xml.etree.ElementTree as ET
from server import summarize, calendar, rss, safe_sources

NOW = 1792742400

def event(id='a', **changes):
    return dict(event_id=id, root_id=id, type='reset', state='scheduled', published_at='2026-10-23T08:00:00Z', window_end='2026-10-23T18:00:00Z', source_urls=['https://x.com/thsottiaux/status/1'], **changes)

def data(events):
    return dict(events=events, source_healthy=True, sync_ok=True, source_checked_at='2026-10-23T08:00:00Z')

class AnnouncementSemantics(unittest.TestCase):
    def test_deadline_passing_never_means_completed(self):
        d=data([event()]); s=summarize(d,NOW+40000)
        self.assertIsNone(s['upcoming']); self.assertIsNone(s['latest_completed'])
    def test_stale_source_cannot_create_active_countdown(self):
        d=data([event()]); self.assertIsNone(summarize(d,NOW+1801)['upcoming'])
    def test_new_completion_does_not_resurrect_old_forecast(self):
        older=event('old'); newer={**event('new'),'state':'completed','published_at':'2026-10-23T08:01:00Z'}
        self.assertIsNone(summarize(data([older,newer]),NOW+100)['upcoming'])
    def test_same_thread_completion_supersedes_forecast(self):
        forecast=event(); complete={**event('reply'),'root_id':'a','state':'completed','published_at':'2026-10-23T07:59:00Z'}
        self.assertIsNone(summarize(data([forecast,complete]),NOW)['upcoming'])
    def test_future_healthy_forecast_is_active(self):
        s=summarize(data([event()]),NOW); self.assertEqual(s['upcoming']['event_id'],'a')
    def test_later_completion_reply_supersedes_an_earlier_forecast(self):
        completion={**event('thread'),'state':'completed','published_at':'2026-10-22T08:00:00Z',
                    'activity_at':'2026-10-23T08:01:00Z','completed_announced_at':'2026-10-23T08:01:00Z'}
        s=summarize(data([event('forecast'),completion]),NOW+100)
        self.assertIsNone(s['upcoming'])
        self.assertEqual(s['latest_completed']['event_id'],'thread')
    def test_calendar_refuses_stale_forecast(self):
        d=data([event()]); d['summary']=summarize(d,NOW+1801); self.assertIsNone(calendar(d))
    def test_rss_keeps_stable_identity_and_xml_escapes(self):
        e=event('a&b'); d=data([e]); d['summary']=summarize(d,NOW)
        self.assertEqual(ET.fromstring(rss(d)).find('channel/item/guid').text,'reset-radar:a&b')
    def test_unsafe_source_urls_are_removed(self):
        self.assertEqual(safe_sources(['javascript:alert(1)','https://evil.example/x','https://x.com/a']),['https://x.com/a'])

if __name__=='__main__':unittest.main()
