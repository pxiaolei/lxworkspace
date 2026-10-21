import tempfile
import unittest
from pathlib import Path

import yaml
from _support import FIXTURE, ROOT, require


class InputPlanTests(unittest.TestCase):
    def setUp(self):
        self.api = require(self, 'input_plan')

    def test_all_is_five_types_eight_exact_targets(self):
        plan = self.api.build_plan(FIXTURE, types='all')
        self.assertEqual(len(plan['targets']), 8)
        self.assertEqual(len({r['type'] for r in plan['targets']}), 5)
        popup = [r for r in plan['targets'] if r['type'] == 'popup']
        self.assertEqual([(r['width'], r['height']) for r in popup], [(534, 712), (884, 1178)])
        self.assertEqual(popup[0]['max_bytes'], 307200)
        self.assertEqual(popup[0]['filename'], '弹窗-534x712-五一早鸟卡.png')

    def test_type_and_size_filter_and_override(self):
        self.assertEqual(len(self.api.build_plan(FIXTURE, types='popup')['targets']), 2)
        selected = self.api.build_plan(FIXTURE, types='popup,splash', sizes='884x1178')['targets']
        self.assertEqual([r['id'] for r in selected], ['popup:884x1178'])
        self.assertEqual(len(self.api.build_plan(FIXTURE, sizes='640x960')['targets']), 1)

    def test_unknown_and_empty_selection_fail(self):
        for args in ({'types': 'other'}, {'sizes': '884x1179'}, {'types': 'popup', 'sizes': '640x960'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.api.build_plan(FIXTURE, **args)

    def test_txt_and_yaml_have_same_content(self):
        one = self.api.build_plan(FIXTURE)
        two = self.api.build_plan(FIXTURE.with_suffix('.txt'))
        self.assertEqual(one['activity'], two['activity'])
        self.assertEqual(one['targets'], two['targets'])

    def test_per_type_copy_and_no_splash_button(self):
        rows = self.api.build_plan(FIXTURE)['targets']
        self.assertEqual(rows[0]['text']['title'], ['五一6天全免佣 购卡仅需50元'])
        splash = next(r for r in rows if r['type'] == 'splash')
        self.assertNotIn('button', splash['text'])
        self.assertEqual(splash['text']['amounts'], ['早鸟价 ¥50', '正式价 ¥100'])

    def test_added_size_requires_configuration_only(self):
        specs = yaml.safe_load((ROOT / 'specs.yaml').read_text())
        specs['types']['popup']['sizes'].append([600, 800])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'specs.yaml'; p.write_text(yaml.safe_dump(specs, allow_unicode=True))
            rows = self.api.build_plan(FIXTURE, types='popup', specs_path=p)['targets']
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[-1]['id'], 'popup:600x800')

    def test_sample_stays_in_scope_and_uses_two_families(self):
        plan = self.api.build_plan(FIXTURE)
        self.assertEqual([r['id'] for r in self.api.sample_targets(plan)], ['home_banner:690x160', 'splash:1242x2208'])
        plan = self.api.build_plan(FIXTURE, types='popup')
        self.assertEqual(len(self.api.sample_targets(plan)), 1)

    def test_missing_fields_are_omitted_and_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'input.yaml'
            p.write_text('brand: 宏鹄\nactivity_name: 单标题\ntitle: 免佣活动\n')
            row = self.api.build_plan(p)['targets'][0]
            self.assertEqual(row['text'], {'title': ['免佣活动']})
            p.write_text('brand: 宏鹄\nactivity_name: ../escape\ntitle: 免佣\n')
            with self.assertRaises(ValueError): self.api.build_plan(p)

    def test_numbers_must_be_explicit_strings_and_unknown_fields_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'input.yaml'
            for extra in ['amounts: 50', 'unknown: surprise']:
                p.write_text('brand: 宏鹄\nactivity_name: 测试\ntitle: 免佣\n'+extra+'\n')
                with self.subTest(extra=extra), self.assertRaises(ValueError): self.api.build_plan(p)

    def test_duplicate_source_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'input.yaml'
            p.write_text('brand: 宏鹄\nactivity_name: 测试\ntitle: 50元\ntitle: 500元\n')
            with self.assertRaises(ValueError): self.api.build_plan(p)

    def test_check_rejects_invalid_dimensions_duplicate_sizes_and_unsupported_request(self):
        from copy import deepcopy
        original=yaml.safe_load((ROOT/'specs.yaml').read_text())
        variants=[]
        broken=deepcopy(original); broken['types']['popup']['sizes']=[[0,712]]; variants.append(broken)
        broken=deepcopy(original); broken['types']['popup']['sizes'].append([534,712]); variants.append(broken)
        broken=deepcopy(original); broken['types']['home_banner']['resolution']='2K'; variants.append(broken)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'specs.yaml'
            for spec in variants:
                p.write_text(yaml.safe_dump(spec,allow_unicode=True))
                with self.subTest(spec=spec),self.assertRaises(ValueError): self.api.load_specs(p)
