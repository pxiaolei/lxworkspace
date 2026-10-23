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

    def test_design_fields_are_not_visible_copy_and_absent_reference_is_neutral(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'activity.yaml'
            p.write_text('品牌: 测试品牌\n活动名: 通用活动\n标题: 本周免佣\n场景: 城市道路，不要花台\n构图: 纯文字居中\n')
            plan=self.api.build_plan(p)
            self.assertIsNone(plan['activity']['reference_set'])
            self.assertEqual(plan['targets'][0]['text'],{'title':['本周免佣']})
            self.assertEqual(plan['targets'][0]['scene'],'城市道路，不要花台')
            self.assertEqual(plan['targets'][0]['composition'],'纯文字居中')
            for extra in ['scene: []','composition: 12','scene: " "','reference_set: false']:
                p.write_text('brand: 测试\nactivity_name: 无效\ntitle: 标题\n'+extra+'\n')
                with self.subTest(extra=extra),self.assertRaises(ValueError): self.api.build_plan(p)

    def test_splash_button_and_design_require_explicit_type_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'activity.yaml'
            p.write_text('brand: 测试\nactivity_name: 分类型\ntitle: 通用标题\nbutton: 查看详情\nscene: 通用场景\ntype_text:\n  splash:\n    按钮文案: 了解活动\n    场景: 抽象图形\n    构图: 居中\n')
            plan=self.api.build_plan(p)
            row=next(r for r in plan['targets'] if r['type']=='splash')
            self.assertEqual(row['text'],{'title':['通用标题'],'button':['了解活动']})
            self.assertEqual(row['scene'],'抽象图形')
            self.assertEqual(row['composition'],'居中')
            self.assertEqual(plan['targets'][0]['scene'],'通用场景')

    def test_design_change_invalidates_existing_plan_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'activity.yaml'
            original='brand: 测试\nactivity_name: 场景变化\ntitle: 标题\n'
            p.write_text(original+'scene: 城市道路\n')
            first=self.api.build_plan(p)
            p.write_text(original+'scene: 纯色背景\n')
            second=self.api.build_plan(p)
            self.assertNotEqual(first['plan_hash'],second['plan_hash'])
            self.assertEqual(first['targets'][0]['text'],second['targets'][0]['text'])

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
