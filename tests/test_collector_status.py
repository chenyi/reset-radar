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
        self.assertEqual(result,{'buffer_files':1,'buffer_bytes':3,'oldest_buffer_age_s':20,'checkpoint_age_s':10})
