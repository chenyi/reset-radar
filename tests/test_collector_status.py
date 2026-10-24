import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('collector_status',Path(__file__).resolve().parent.parent/'scripts/collector_status.py')
status=importlib.util.module_from_spec(spec);spec.loader.exec_module(status)


class CollectorStatusTest(unittest.TestCase):
    def test_counts_only_buffer_files_and_uses_persistent_checkpoint(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); checkpoint=root/'checkpoint'
            checkpoint.write_text('state');os.utime(checkpoint,(90,90))
            (root/'ilogtail_config.json').write_text(json.dumps({'check_point_filename':str(checkpoint)}))
            for name in ['logtail_buffer_file_1','other.log']:
                p=root/name;p.write_bytes(b'abc');os.utime(p,(80,80))
            result=status.snapshot(root,100)
        self.assertEqual(result,{'buffer_files':1,'buffer_bytes':3,'oldest_buffer_age_s':20,'checkpoint_age_s':10,
                                 'access_send_bytes_total':-1,'app_send_bytes_total':-1,
                                 'access_send_errors_total':-1,'app_send_errors_total':-1})

    def test_native_metrics_select_only_our_flushers(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'ilogtail_config.json').write_text('{}')
            (root/'self_metrics').mkdir()
            items=[{'__labels__':{'project':project,'plugin_type':'flusher_sls','logstore':store},
                    '__value__':{'in_size_bytes':size,'network_error_total':2}}
                   for project,store,size in [('other','app',9999),('reset-radar-chenyi-sg','app',123)]]
            (root/'self_metrics/self_metrics.log').write_text('\n'.join(json.dumps(i) for i in items))
            result=status.snapshot(root,100)
        self.assertEqual(result['app_send_bytes_total'],123)
        self.assertEqual(result['app_send_errors_total'],2)
        self.assertEqual(result['access_send_bytes_total'],-1)
