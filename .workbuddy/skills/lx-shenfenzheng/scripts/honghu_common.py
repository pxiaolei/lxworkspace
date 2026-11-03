"""Shared local safeguards for the three Honghu driver skills; no import side effects."""
import hashlib
import io
import json
import os
from pathlib import Path
import time
import threading
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('接口重定向已阻断，请联系维护者核对')


def request_json(url, body, cookie=None, form=False, extra_headers=None):
    if urllib.parse.urlsplit(url).scheme != 'https':
        raise ValueError('只允许 HTTPS')
    payload = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
    headers = {'Content-Type': 'application/x-www-form-urlencoded' if form else 'application/json'}
    headers.update(extra_headers or {})
    if cookie:
        headers['Cookie'] = cookie
    request = urllib.request.Request(url, data=payload, headers=headers, method='POST')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
    except Exception:
        raise ValueError('接口请求失败；未自动重试，请核对网络或联系维护者') from None
    if not isinstance(result, dict):
        raise ValueError('接口响应结构无效')
    return result


def no_symlink(path):
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('路径不允许符号链接')
    return path


def read_ticket(cache, username, company_id):
    if not username or not company_id:
        raise ValueError('必须显式指定本人 --username 与 --company_id')
    path = no_symlink(cache)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))[username]
        if str(value.get('company_id')) != str(company_id):
            raise ValueError()
        ticket = value['ticket']
        if not isinstance(ticket, str) or not ticket or any(c in ticket for c in '\r\n;') or float(value['expire_time']) < time.time()+60:
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('指定账号/租户没有有效缓存，请用本人账号在本机窗口重新登录；不切换其他账号') from None
    return ticket


def preview_hash(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def batch_plan(args, suffix):
    source = no_symlink(args.input)
    output = no_symlink(args.output or source.with_name(source.stem + suffix + '.xlsx'))
    if output.suffix.lower() != '.xlsx':
        raise ValueError('输出必须是 .xlsx')
    if not source.is_file() or source.suffix.lower() not in {'.xls', '.xlsx'}:
        raise ValueError('输入必须是已有的 xls/xlsx 文件')
    if output.exists() or output == source or not output.parent.is_dir():
        raise ValueError('输出已存在、与输入相同或父目录不存在；请另选新文件')
    if not 1 <= args.threads <= 10:
        raise ValueError('线程数只支持 1 至 10')
    if not args.username or not args.company_id.isdecimal():
        raise ValueError('必须明确本人账号及数字 company_id')
    plan = {'input': str(source), 'input_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'output': str(output), 'username': args.username, 'company_id': args.company_id,
            'threads': args.threads, 'id_col': args.id_col, 'kind': suffix,
            'fields': getattr(args, 'fields', None), 'col': getattr(args, 'col', None)}
    return plan


def pending_driver_ids(frame, id_col, output_columns):
    import pandas as pd
    return sorted({str(row[id_col]).strip() for _, row in frame.iterrows()
                   if pd.notna(row[id_col]) and str(row[id_col]).strip()
                   and any(column not in frame.columns or pd.isna(row[column]) or not str(row[column]).strip()
                           for column in output_columns)})


def authorize_batch(args, suffix, parser, frame, id_col, output_columns):
    try:
        plan = batch_plan(args, suffix)
    except ValueError as exc:
        parser.error(str(exc))
    plan.update(rows=len(frame), unique_drivers=len(set(frame[id_col].dropna().astype(str).str.strip()) - {''}),
                output_columns=output_columns, pending_drivers=len(pending_driver_ids(frame, id_col, output_columns)))
    digest = preview_hash(plan)
    # The binding includes the account, but previews never disclose the account or driver IDs.
    print(json.dumps({k: v for k, v in plan.items() if k != 'username'} | {'preview_hash': digest}, ensure_ascii=False))
    if not args.confirmed:
        return False
    if args.expected_preview_hash != digest:
        parser.error('确认 hash 不一致，零请求、零写入')
    args.output = plan['output']
    return True


def add_confirmation(parser):
    parser.add_argument('--confirmed', action='store_true')
    parser.add_argument('--expected-preview-hash')


def driver_column(columns, specified):
    if specified:
        if specified not in columns:
            raise ValueError('指定的司机ID列不存在')
        return specified
    candidates = [c for c in columns if 'driver' in c.lower() or '司机' in c] or [c for c in columns if 'id' in c.lower()]
    if len(candidates) != 1:
        raise ValueError('司机ID列缺失或有歧义，请显式指定 --id_col')
    return candidates[0]


def checked_response(result, stop):
    if not isinstance(result, dict) or 'errno' not in result:
        raise ValueError('请求失败或响应缺少 errno')
    code = str(result['errno'])
    if code != '0':
        if code in {'13005', '13006', '13008'}:
            stop.set()
        reason = {'13005': '会话失效', '13006': '权限不足', '13008': '账号或商户禁用'}.get(code, '请求失败')
        raise ValueError(reason)
    return result.get('data')


def first_result(todo, fetch):
    if not todo:
        return None
    result = fetch(todo[0])
    if result[2]:
        raise ValueError('单点校验失败：' + '；'.join(result[2]) + '；未启动批量')
    return result


def write_new_workbook(frame, output):
    import pandas as pd
    from openpyxl import load_workbook
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False, engine='openpyxl')
    workbook = load_workbook(io.BytesIO(buffer.getvalue()))
    # Treat user-supplied strings as data, including strings beginning with '='.
    for row in workbook.active:
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = 's'
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    path = no_symlink(output)
    with path.open('xb') as stream:
        stream.write(buffer.getvalue())
    saved = load_workbook(path, data_only=False, read_only=True)
    actual = list(saved.active.values)
    saved.close()
    expected = [tuple(str(v) for v in frame.columns)] + [tuple(None if pd.isna(v) or str(v) == '' else v for v in row) for row in frame.itertuples(index=False, name=None)]
    if actual != expected:
        raise ValueError('输出逐单元格读回失败；保留文件，不能报告成功')
    return {'rows': len(frame), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def save_login(cache_path, username, company_id, ticket):
    """Called only after the user clicks Login and Save in the local window."""
    import tempfile
    path = no_symlink(cache_path)
    cache = {}
    if path.exists():
        try:
            cache = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(cache, dict):
                raise ValueError()
        except (OSError, ValueError):
            raise ValueError('现有缓存不可读取；保留原文件并联系维护者') from None
    cache[username] = {'ticket': ticket, 'company_id': str(company_id), 'expire_time': int(time.time())+5400}
    # Temporary secret bytes are mode 0600 and are atomically moved to the intended cache.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.login-', suffix='.tmp', delete=False) as stream:
        json.dump(cache, stream)
        temporary = stream.name
    no_symlink(path)
    os.replace(temporary, path)
    if read_ticket(path, username, company_id) != ticket:
        raise ValueError('缓存写后读回失败；不能报告登录成功')


def perform_login(username, password, company_id, cache):
    body = dict(LOGIN_PARAMS, cell=username, password=password)
    result = request_json(LOGIN_URL, {'q': json.dumps(body, ensure_ascii=False, separators=(',', ':'))}, form=True,
                          extra_headers={'Accept': 'application/json, text/plain, */*',
                                         'Origin': 'https://workbench.honghusaas.com',
                                         'Referer': 'https://workbench.honghusaas.com/',
                                         'Accept-Language': 'zh-CN,zh;q=0.9',
                                         'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})
    data = checked_response(result, threading.Event())
    ticket = result.get('ticket') or (data.get('ticket') if isinstance(data, dict) else None)
    if not isinstance(ticket, str) or not ticket or any(c in ticket for c in '\r\n;'):
        raise ValueError('登录响应没有有效会话，未保存')
    save_login(cache, username, company_id, ticket)
    return '登录成功，缓存读回通过'


def login_window(username, company_id, cache):
    try:
        import tkinter as tk
        from tkinter import messagebox
        window = tk.Tk()
    except Exception:
        raise ValueError('本机登录窗口无法启动，请联系对接人；不要在聊天或命令参数中填写密码') from None
    window.title('宏鹄本人账号登录')
    tk.Label(window, text=f'账号：{username}\n租户：{company_id}\n登录可能使同账号的旧会话失效。').pack(padx=20, pady=12)
    tk.Label(window, text='密码仅在本机输入；点击登录并保存才发送请求。').pack()
    password = tk.Entry(window, show='*', width=36)
    password.pack(padx=20, pady=12)
    outcome = {'ok': False}

    def submit():
        value = password.get()
        if not value:
            return
        password.delete(0, tk.END)
        try:
            perform_login(username, value, company_id, cache)
        except ValueError as exc:
            messagebox.showerror('登录未完成', str(exc), parent=window)
            window.destroy()
            return
        outcome['ok'] = True
        window.destroy()

    tk.Button(window, text='登录并保存', command=submit).pack(pady=8)
    tk.Button(window, text='取消', command=window.destroy).pack(pady=8)
    window.mainloop()
    return outcome['ok']


def login_main(argv, cache):
    import argparse
    parser = argparse.ArgumentParser(description='宏鹄本机登录窗口，默认仅预览')
    parser.add_argument('--username', required=True)
    parser.add_argument('--company_id', required=True)
    add_confirmation(parser)
    args = parser.parse_args(argv)
    if not args.company_id.isdecimal() or not args.username.strip():
        parser.error('必须明确本人账号及数字 company_id')
    plan = {'username': args.username, 'company_id': args.company_id, 'cache': str(no_symlink(cache)), 'action': 'login-gui'}
    digest = preview_hash(plan)
    print(json.dumps({'action': 'login-gui', 'company_id': args.company_id, 'preview_hash': digest}, ensure_ascii=False))
    if not args.confirmed:
        return 0
    if args.expected_preview_hash != digest:
        parser.error('确认 hash 不一致，未打开窗口、未发送请求')
    if login_window(args.username, args.company_id, cache):
        print('登录成功，缓存读回通过；未输出账号密码或 ticket')
        return 0
    print('登录取消或未完成，未声明成功')
    return 2


# Protocol parameters retained from the imported implementation; HTTPS live acceptance pending.
LOGIN_URL = 'https://epassport.honghusaas.com/passport/login/v5/signInByPassword?wsgsig=dd03-3TV0eVuoNmHMvgah07hUIaRv5fOKys%2FkFuOi69sw5fOJvWEwg7BXIrRTMWHJvgMmD3SRJhzTLgFITCBQGKqm6hRl1cI4viIhb7TWHhnQMtEJugxjb7ZXIdoy%2BWA'
LOGIN_PARAMS = {'domain': 'https://epassport.honghusaas.com', 'source': 70001, 'appid': 140300, 'role': 3000, 'api_version': '1.0.1', 'lang': 'zh-CN', 'country_id': 156, 'auto_fill_phone': 'true', 'password_encrypt_type': 0, 'redirectUrl': 'https://api.honghusaas.com/biz-api/v1/workbench/rbac/user/login?redirectUrl=%2F%2Fworkbench.honghusaas.com%2F%23%2Fasset-management%2Fdriver-menu%2Fextensive-driver-list&login_type=2', 'imei': '1a10e889fda8b330228ed2a3a94e2e13', 'country_calling_code': '+86'}
