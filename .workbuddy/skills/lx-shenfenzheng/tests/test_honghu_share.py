"""Offline regression tests; all identities, tickets and driver records are fictional."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import pandas as pd

SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS / 'lx-shenfenzheng/scripts'))
ENTRIES = {
    'lx-shenfenzheng': 'fetch_idcards.py',
    'lx-cheliang': 'fetch_carinfo.py',
    'lx-sijiyue': 'fetch_balance.py',
}


def load(skill, file):
    path = SKILLS / skill / 'scripts' / file
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(skill.replace('-', '_') + file[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SharingTests(unittest.TestCase):
    def test_missing_username_never_selects_another_account(self):
        for skill, file in ENTRIES.items():
            with self.subTest(skill=skill), tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
                mod = load(skill, file)
                cache = Path(tmp) / 'ticket_cache.json'
                cache.write_text(json.dumps({'fixture-account': {'ticket': 'fixture-ticket', 'expire_time': time.time()+900}}))
                with patch.object(mod, 'CACHE', str(cache)), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises((ValueError, SystemExit)):
                        mod.load_ticket()

    def test_login_preview_never_opens_window_or_requests_credentials(self):
        import honghu_common as common
        for skill in ENTRIES:
            with self.subTest(skill=skill), patch.object(common, 'login_window', side_effect=AssertionError('unexpected GUI')), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(load(skill, 'login.py').main(['--username', 'fixture-account', '--company_id', '1312']), 0)

    def test_decryption_session_expiry_is_not_reported_as_empty_data(self):
        for skill in ('lx-shenfenzheng', 'lx-cheliang'):
            with self.subTest(skill=skill):
                mod = load(skill, ENTRIES[skill])
                mod.STOP.clear()
                with patch.object(mod, 'post', return_value={'errno': 13005, 'errmsg': 'fixture'}):
                    with self.assertRaises(ValueError):
                        mod.dec('fixture-cipher')
                self.assertTrue(mod.STOP.is_set())

    def fixture(self, tmp, skill):
        mod = load(skill, ENTRIES[skill])
        source, output, cache = (Path(tmp)/n for n in ('input.xlsx', 'result.xlsx', 'ticket_cache.json'))
        pd.DataFrame({'司机ID': ['0001', '0002']}).to_excel(source, index=False)
        cache.write_text(json.dumps({'fixture-account': {'ticket': 'fixture-ticket', 'company_id': '1312', 'expire_time': time.time()+900}}))
        args = ['--username', 'fixture-account', '--company_id', '1312', '--input', str(source), '--output', str(output), '--threads', '1']
        return mod, source, output, cache, args

    def confirmed(self, mod, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(mod.main(args), 0)
        digest = [json.loads(line)['preview_hash'] for line in out.getvalue().splitlines() if line.startswith('{')][-1]
        return args + ['--confirmed', '--expected-preview-hash', digest]

    def test_preview_and_stale_hash_make_no_requests_or_cache_reads(self):
        import honghu_common as common
        for skill in ENTRIES:
            with self.subTest(skill=skill), tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
                mod, source, output, cache, args = self.fixture(tmp, skill)
                with patch.object(common, 'request_json', side_effect=AssertionError('network')), patch.object(common, 'read_ticket', side_effect=AssertionError('cache read')):
                    command = self.confirmed(mod, args)
                    pd.DataFrame({'司机ID': ['0003']}).to_excel(source, index=False)
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        mod.main(command)
                self.assertFalse(output.exists())

    def test_existing_or_symlink_output_is_refused_before_network(self):
        import honghu_common as common
        for skill in ENTRIES:
            with self.subTest(skill=skill), tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
                mod, source, output, cache, args = self.fixture(tmp, skill)
                command = self.confirmed(mod, args)
                output.write_bytes(b'preserve')
                with patch.object(common, 'request_json', side_effect=AssertionError('network')), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    mod.main(command)
                self.assertEqual(output.read_bytes(), b'preserve')

    def test_exact_account_and_tenant_are_required(self):
        import honghu_common as common
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            cache = Path(tmp)/'ticket_cache.json'
            cache.write_text(json.dumps({'A': {'ticket': 'fixture-ticket', 'company_id': '1312', 'expire_time': time.time()+900}}))
            for username, tenant in [('B', '1312'), ('A', '1639')]:
                with self.assertRaises(ValueError):
                    common.read_ticket(cache, username, tenant)
            self.assertEqual(common.read_ticket(cache, 'A', '1312'), 'fixture-ticket')

    def test_login_saves_without_printing_secrets(self):
        import honghu_common as common
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            cache = Path(tmp)/'ticket_cache.json'
            out = io.StringIO()
            with patch.object(common, 'request_json', return_value={'errno': 0, 'data': {'ticket': 'fixture-secret-ticket'}}), contextlib.redirect_stdout(out):
                self.assertEqual(common.perform_login('fixture-account', 'fixture-password', '1312', cache), '登录成功，缓存读回通过')
            value = json.loads(cache.read_text())['fixture-account']
            self.assertEqual(value['company_id'], '1312')
            self.assertNotIn('fixture-secret-ticket', out.getvalue())
            self.assertNotIn('fixture-password', cache.read_text())
            if sys.platform != 'win32':
                self.assertEqual(cache.stat().st_mode & 0o777, 0o600)

    def test_login_readback_failure_cannot_report_success(self):
        import honghu_common as common
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp, patch.object(common, 'request_json', return_value={'errno': 0, 'ticket': 'fixture-ticket'}), patch.object(common, 'read_ticket', side_effect=ValueError('readback failed')):
            with self.assertRaisesRegex(ValueError, 'readback'):
                common.perform_login('fixture-account', 'fixture-password', '1312', Path(tmp)/'cache.json')

    def test_login_cancel_or_stale_hash_does_not_create_cache(self):
        import honghu_common as common
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            cache = Path(tmp)/'cache.json'
            args = ['--username', 'fixture-account', '--company_id', '1312']
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                common.login_main(args, cache)
            digest = json.loads(out.getvalue())['preview_hash']
            with patch.object(common, 'login_window', return_value=False), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(common.login_main(args+['--confirmed', '--expected-preview-hash', digest], cache), 2)
            with patch.object(common, 'login_window', side_effect=AssertionError('GUI')), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                common.login_main(args+['--confirmed', '--expected-preview-hash', 'stale'], cache)
            self.assertFalse(cache.exists())

    def test_sample_rejection_prevents_batch_and_output(self):
        import honghu_common as common
        for code in [13005, 13006, 13008]:
            for skill in ENTRIES:
                with self.subTest(skill=skill, code=code), tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
                    mod, source, output, cache, args = self.fixture(tmp, skill)
                    command = self.confirmed(mod, args)
                    calls = []
                    def response(*a, **k):
                        calls.append(a)
                        return {'errno': code, 'errmsg': 'fixture-provider-message'}
                    with patch.object(mod, 'CACHE', str(cache)), patch.object(common, 'request_json', side_effect=response), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                        mod.main(command)
                    self.assertEqual(len(calls), 1)
                    self.assertFalse(output.exists())

    def test_batch_readback_preserves_ids_multicar_and_empty_withdrawal(self):
        import honghu_common as common
        for skill in ENTRIES:
            with self.subTest(skill=skill), tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
                mod, source, output, cache, args = self.fixture(tmp, skill)
                original = source.read_bytes()
                command = self.confirmed(mod, args)
                calls = []
                def response(url, body, *a, **k):
                    calls.append((url, body))
                    if url.endswith('clarify'):
                        return {'errno': 0, 'data': {'decrypt_info': 'fictional-'+body['encrypt_info']}}
                    if skill == 'lx-shenfenzheng':
                        return {'errno': 0, 'data': {'basic': {'encrypt_id_no': 'id-'+body['driver_id']}}}
                    if skill == 'lx-cheliang':
                        return {'errno': 0, 'data': [
                            {'basic': {'in_use': 0, 'vin': 'fixture-vin-2', 'encrypt_plate_no': 'plate2', 'encrypt_owner': 'owner2', 'brand_desc': '车系2', 'color': '白'}},
                            {'basic': {'in_use': 1, 'vin': 'fixture-vin-1', 'encrypt_plate_no': 'plate1', 'encrypt_owner': 'owner1', 'brand_desc': '车系1', 'color': '黑'}}]}
                    return {'errno': 0, 'data': {'real_balance': '0.00', 'withdraw_balance': '', 'is_sign': False}}
                out = io.StringIO()
                with patch.object(mod, 'CACHE', str(cache)), patch.object(common, 'request_json', side_effect=response), contextlib.redirect_stdout(out):
                    self.assertEqual(mod.main(command), 0)
                saved = pd.read_excel(output, dtype=str, keep_default_na=False)
                self.assertEqual(saved['司机ID'].tolist(), ['0001', '0002'])
                if skill == 'lx-shenfenzheng':
                    self.assertEqual(saved['身份证号'].tolist(), ['fictional-id-0001', 'fictional-id-0002'])
                elif skill == 'lx-cheliang':
                    self.assertEqual(saved['车辆识别代码'].tolist(), ['fixture-vin-1']*2)
                    self.assertEqual(saved['车牌号2'].tolist(), ['fictional-plate2']*2)
                else:
                    self.assertEqual(saved['账户余额'].tolist(), ['0.00']*2)
                    self.assertEqual(saved['可提现余额'].tolist(), ['', ''])
                    self.assertEqual(saved['免密状态'].tolist(), ['否', '否'])
                self.assertEqual(source.read_bytes(), original)
                self.assertNotIn('fictional-', out.getvalue())
                self.assertNotIn('fixture-ticket', out.getvalue())
                self.assertEqual(sum(1 for url, body in calls if not url.endswith('clarify')), 2)

    def test_workbook_preserves_literal_formula_strings_and_refuses_overwrite(self):
        import honghu_common as common
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            output = Path(tmp)/'out.xlsx'
            common.write_new_workbook(pd.DataFrame({'司机ID': ['0001'], '备注': ['=1+2']}), output)
            from openpyxl import load_workbook
            workbook = load_workbook(output)
            self.assertEqual(workbook.active['B2'].data_type, 's')
            workbook.close()
            before = output.read_bytes()
            with self.assertRaises(FileExistsError):
                common.write_new_workbook(pd.DataFrame({'司机ID': ['0002']}), output)
            self.assertEqual(output.read_bytes(), before)

    def test_workbook_readback_mismatch_cannot_report_success(self):
        import honghu_common as common
        import openpyxl
        original = openpyxl.load_workbook
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            output = Path(tmp)/'out.xlsx'
            def mismatching(path, *args, **kwargs):
                book = original(path, *args, **kwargs)
                if path == output:
                    # An independent reader sees different persisted cells.
                    book.close()
                    book = original(path)
                    book.active['A2'] = 'wrong-driver'
                return book
            with patch.object(openpyxl, 'load_workbook', side_effect=mismatching), self.assertRaisesRegex(ValueError, '读回失败'):
                common.write_new_workbook(pd.DataFrame({'司机ID':['0001']}), output)
            self.assertTrue(output.is_file())

    def test_transport_uses_form_for_balance_and_rejects_http_redirects(self):
        import honghu_common as common
        from unittest.mock import MagicMock
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"errno":0,"data":{}}'
        with patch.object(common.urllib.request, 'build_opener', return_value=opener):
            common.request_json('https://api.honghusaas.com/test', {'driver_id':'0001','company_id':'1312'}, 'fixture-cookie', form=True)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.data, b'driver_id=0001&company_id=1312')
        with self.assertRaises(ValueError):
            common.request_json('http://api.honghusaas.com/test', {})
        with self.assertRaises(ValueError):
            common.NoRedirect().redirect_request(request, None, 302, '', {}, 'http://other.example')

    def test_missing_balance_flags_stay_empty(self):
        mod = load('lx-sijiyue', 'fetch_balance.py')
        mod.STOP.clear()
        with patch.object(mod, 'post_form', return_value={'errno': 0, 'data': {'real_balance': '0.00', 'withdraw_balance': None, 'is_sign': None}}):
            did, values, notes = mod.fetch_one(('0001', '1312'))
        self.assertEqual(values['可提现余额'], '')
        self.assertEqual(values['免密状态'], '')

    def test_empty_data_never_passes_sample_validation(self):
        import honghu_common as common
        for skill in ENTRIES:
            with self.subTest(skill=skill):
                mod = load(skill, ENTRIES[skill])
                mod.STOP.clear()
                fn = (lambda item: mod.fetch_one(item, ['plate'])) if skill == 'lx-cheliang' else mod.fetch_one
                with patch.object(common, 'request_json', return_value={'errno': 0, 'data': [] if skill == 'lx-cheliang' else {}}), self.assertRaises(ValueError):
                    common.first_result([('0001', '1312')], fn)

    def test_ambiguous_id_column_is_rejected(self):
        import honghu_common as common
        with self.assertRaises(ValueError):
            common.driver_column(['driver_name', '司机ID'], None)
        self.assertEqual(common.driver_column(['driver_name', '司机ID'], '司机ID'), '司机ID')

    def test_symlink_output_and_cache_are_refused(self):
        import honghu_common as common
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            target = Path(tmp)/'original'
            target.write_text('preserve')
            link = Path(tmp)/'alias'
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                common.write_new_workbook(pd.DataFrame({'司机ID': ['0001']}), link)
            with self.assertRaises(ValueError):
                common.save_login(link, 'fixture-account', '1312', 'fixture-ticket')
            self.assertEqual(target.read_text(), 'preserve')

    def test_preview_contains_row_and_unique_driver_counts(self):
        for skill in ENTRIES:
            with self.subTest(skill=skill), tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
                mod, source, output, cache, args = self.fixture(tmp, skill)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    mod.main(args)
                plan = [json.loads(line) for line in out.getvalue().splitlines() if line.startswith('{')][-1]
                self.assertEqual(plan.get('rows'), 2)
                self.assertEqual(plan.get('unique_drivers'), 2)

    def test_xlsx_disguised_as_xls_keeps_leading_zero_ids(self):
        for skill in ENTRIES:
            with self.subTest(skill=skill), tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
                mod, source, output, cache, args = self.fixture(tmp, skill)
                disguised = source.with_suffix('.xls')
                disguised.write_bytes(source.read_bytes())
                self.assertEqual(mod.read_table(str(disguised))['司机ID'].tolist(), ['0001', '0002'])

    def test_duplicate_driver_rows_are_queried_once_and_existing_cells_preserved(self):
        mod = load('lx-shenfenzheng', 'fetch_idcards.py')
        import honghu_common as common
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            mod, source, output, cache, args = self.fixture(tmp, 'lx-shenfenzheng')
            pd.DataFrame({'司机ID': ['0001', '0001'], '身份证号': ['', 'preserve-existing']}).to_excel(source, index=False)
            command = self.confirmed(mod, args)
            calls = []
            def response(url, body, *a, **k):
                calls.append(url)
                return {'errno': 0, 'data': {'decrypt_info': 'fictional-value'}} if url.endswith('clarify') else {'errno': 0, 'data': {'basic': {'encrypt_id_no': 'cipher'}}}
            with patch.object(mod, 'CACHE', str(cache)), patch.object(common, 'request_json', side_effect=response), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mod.main(command), 0)
            self.assertEqual(pd.read_excel(output, dtype=str)['身份证号'].tolist(), ['fictional-value', 'preserve-existing'])
            self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
