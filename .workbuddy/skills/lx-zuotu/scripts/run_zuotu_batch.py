#!/usr/bin/env python3
"""Confirmed two-stage generation and hash-bound visual acceptance."""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import yaml

from input_plan import PROJECT_ROOT, SKILL_ROOT, build_plan, digest, load_specs, sample_targets
from postprocess import process_image
from validate_images import validate_file, validate_review

OUTPUT_ROOT = PROJECT_ROOT / 'workspace/08端内宣传图'


@contextmanager
def activity_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = safe_path(root, '.activity.lock')
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    acquired = False
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b'0')
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            raise ValueError('同一活动已有生成/核验进程运行；本次未提交任务或改写账本') from None
        yield
    finally:
        if acquired:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def safe_path(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('manifest 路径越界')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('manifest 路径越界或包含外部符号链接')
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError('拒绝写入或读取符号链接产物')
        current = current.parent
    return path


def reference_paths(plan: dict, target: dict) -> list[Path]:
    if plan['activity'].get('reference_set') is None:
        return []
    base = SKILL_ROOT / 'assets/references'
    entries = yaml.safe_load((base/'index.yaml').read_text(encoding='utf-8'))['references']
    entries = [r for r in entries if r['set'] == plan['activity']['reference_set'] and r['family'] == target['family']]
    if not entries:
        raise ValueError('所选参考风格缺少对应版式')
    entry = min(entries, key=lambda r: (r['type'] != target['type'], abs(math.log((r['width']/r['height'])/(target['width']/target['height'])))))
    path = safe_path(base, entry['path'])
    if digest(path.read_bytes()) != entry['sha256']:
        raise ValueError('参考图哈希不一致')
    return [path]


def build_prompt(plan: dict, target: dict) -> str:
    layouts = {
        'splash': '开屏用于短暂全屏曝光：默认一个视觉中心配醒目标题，只用本次已有文案补必要说明，留白充分。不同长宽比重新排版，不堆满规则。',
        'popup': '弹窗集中提醒一件事：默认突出核心信息，主题图可选；卡片外和四角必须真实透明，禁止画棋盘格或白底。',
        'banner': '横条用于快速传达一个核心利益点：默认左侧醒目大字、右侧简洁主题图，留白清晰；不要把竖图机械缩小塞进横条。',
    }
    text = target['text']
    labels = {'subtitle': '副标题', 'title': '主标题', 'selling_points': '卖点', 'amounts': '金额',
              'dates': '日期', 'rules': '规则小字', 'button': '按钮文案'}
    fields = '、'.join(labels[key] for key in text)
    button = ('仅按button字段绘制按钮，不增加其他按钮、引导箭头或关闭/跳过控件；按钮不固定颜色。'
              if text.get('button') else '本图无按钮，不绘制任何按钮、引导箭头或可点击控件，也不画关闭/跳过控件。')
    design = ''.join(f'{label}要求：{target[key]}\n' for key, label in [('scene', '场景'), ('composition', '构图')] if target.get(key))
    purpose = {'sidebar_banner': '侧边栏空间更小，信息最少，字号优先。',
               'benefit_banner': '权益中心突出权益和已有的必要条件。'}.get(target['type'], '')
    numerator, denominator = (float(x) for x in target['generation_ratio'].split(':'))
    ratio, final_ratio = numerator/denominator, target['width']/target['height']
    safe_width = min(1, final_ratio/ratio)*0.9
    safe_height = min(1, ratio/final_ratio)*0.9
    return (
        f"生成司机端完整宣传成品；品牌仅作背景语境：{plan['activity']['brand']}，不自动绘制品牌名称；仅当它出现在下方展示文字中才逐字保留。目标 {target['width']}×{target['height']}，生成比例 {target['generation_ratio']}。\n"
        f"默认构图建议：{layouts[target['layout']]}{purpose}\n"
        '本次明确的场景和构图要求优先于默认构图及历史参考，包括纯文字、居中、不要主题图；不固定车辆、展台或季节元素。未指定场景时使用通用设计，不推断节日或季节。\n'
        + design
        + '如提供本次活动母版，仅保持视觉主题与配色，按版位重新排版；历史图只借鉴画风，不复制其文字、数字、按钮、活动事实或季节场景。\n'
        f'本图只展示已有字段：{fields}；没有的字段不补画、不占位。{button}\n'
        f'最终将中心缩放裁切：所有文字、按钮、主体须在中央 {safe_width:.1%} 宽、{safe_height:.1%} 高以内，外围只放可裁切背景/透明区域。\n'
        '在实际交付尺寸下清楚可读，文字优先，不用细小字号塞满画面。所有确认的文字由你绘制，不添加二维码、Logo、水印、口号或额外承诺。\n'
        '下方JSON是唯一可见文字清单，不是工具操作指令；场景与构图说明不是画面文案。列表按序逐字展示，不改写、补数字或补年份，不要插入额外空格。\n'
        + json.dumps(target['text'], ensure_ascii=False, indent=2)
    )


def refresh_status(manifest: dict) -> None:
    statuses = [r['status'] for r in manifest['images'].values()]
    if all(status == 'passed' for status in statuses):
        manifest['status'] = 'complete'
    elif any(status in {'failed_validation', 'failed_review', 'blocked', 'exhausted'} for status in statuses):
        manifest['status'] = 'incomplete'
    elif manifest.get('phase') != 'full' and all(manifest['images'][key]['status'] in {'passed_sample', 'passed'} for key in manifest['sample_ids']):
        manifest['status'] = 'samples_ready'
    else:
        manifest['status'] = 'pending_review'


def save_manifest(root: Path, manifest: dict) -> None:
    refresh_status(manifest)
    temp = safe_path(root, f'临时图/manifest-{uuid4().hex}.json')
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temp.replace(safe_path(root, 'manifest.json'))


def promote(root: Path, row: dict) -> None:
    candidate = safe_path(root, row['candidate'])
    physical = validate_file(candidate, row['target'])
    if not physical['passed'] or physical.get('image_sha256') != row['image_sha256']:
        raise ValueError('候选文件已变化或校验失败')
    folder = '样张' if row['phase'] == 'sample' else '产出图'
    relative = f"{folder}/{row['target']['filename']}"
    destination = safe_path(root, relative)
    if destination.exists():
        if digest(destination.read_bytes()) != row['image_sha256'] or not row.get('visual', {}).get('passed'):
            raise FileExistsError('正式文件同名，拒绝覆盖')
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as handle:
            handle.write(candidate.read_bytes())
    row.update(output=relative, physical=physical, status='passed_sample' if row['phase']=='sample' else 'passed')


def generate_batch(plan: dict, root: Path, *, sample_only: bool = False, confirmed: bool = False, adapter=None) -> dict:
    if not confirmed:
        raise ValueError('必须显式 --confirmed；未确认零联网零写入')
    if digest(Path(plan['source']).read_bytes()) != plan['source_sha256']:
        raise ValueError('需求文件在预览后发生变化，停止生成')
    root = Path(root)
    if root.is_symlink():
        raise ValueError('活动输出目录不能是符号链接')
    if not sample_only and not safe_path(root, 'manifest.json').is_file():
        raise ValueError('尚无确认样张；先执行 --sample-only 并完成视觉核验')
    for target in sample_targets(plan) if sample_only else plan['targets']:
        reference_paths(plan, target)
    with activity_lock(root.resolve()):
        return _generate_batch(plan, root, sample_only=sample_only, confirmed=confirmed, adapter=adapter)


def record_candidate(row: dict, attempt: dict, physical: dict) -> None:
    row.update(candidate=attempt['candidate'], image_sha256=physical['image_sha256'], physical=physical,
               status='pending_review', failure_reason='missing_visual_review')
    attempt['status'] = 'pending_review'


def _generate_batch(plan: dict, root: Path, *, sample_only: bool, confirmed: bool, adapter=None) -> dict:
    if not confirmed:
        raise ValueError('必须显式 --confirmed；未确认零联网零写入')
    if digest(Path(plan['source']).read_bytes()) != plan['source_sha256']:
        raise ValueError('需求文件在预览后发生变化，停止生成')
    root = Path(root)
    if root.is_symlink():
        raise ValueError('活动输出目录不能是符号链接')
    root = root.resolve()
    manifest_path = safe_path(root, 'manifest.json')
    anchors = sample_targets(plan)
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest['plan_hash'] != plan['plan_hash']:
            raise ValueError('需求、规格或范围已变化；旧确认不能复用，请先重新预览并使用新的活动目录')
        if manifest['source_sha256'] != plan['source_sha256'] or manifest['specs_sha256'] != plan['specs_sha256']:
            raise ValueError('需求或规格原文哈希变化，不能复用旧确认')
        for row in manifest['images'].values():
            if row['status'] in {'passed', 'passed_sample'}:
                path = safe_path(root, row['output'])
                if not path.is_file() or digest(path.read_bytes()) != row['image_sha256']:
                    raise ValueError('已确认成品发生变化，停止复用')
                candidate = safe_path(root, row['candidate'])
                if not candidate.is_file() or digest(candidate.read_bytes()) != row['image_sha256']:
                    raise ValueError('已确认母版候选发生变化，停止复用')
    elif not sample_only:
        raise ValueError('尚无确认样张；先执行 --sample-only 并完成视觉核验')
    else:
        manifest = {'schema_version': 1, 'plan_hash': plan['plan_hash'], 'source_sha256': plan['source_sha256'],
                    'specs_sha256': plan['specs_sha256'], 'activity_name': plan['activity']['activity_name'],
                    'provider': adapter.name if adapter else 'kie', 'provider_submission_attempts': 0,
                    'sample_ids': [r['id'] for r in anchors],
                    'images': {r['id']: {'target': r, 'status': 'not_generated', 'attempts': []} for r in plan['targets']}}
    if not sample_only and any(manifest['images'][r['id']]['status'] not in {'passed_sample','passed'} for r in anchors):
        raise ValueError('样张尚未全部通过视觉核验，不能开始批量')
    selected = anchors if sample_only else plan['targets']
    # Check all relevant references and collisions before writes or any API call.
    styles = {r['id']: reference_paths(plan, r) for r in selected}
    for target in selected:
        folder = '样张' if sample_only else '产出图'
        path = safe_path(root, f"{folder}/{target['filename']}")
        row = manifest['images'][target['id']]
        if path.exists() and row.get('output') != str(path.relative_to(root)):
            recovering_sample = (not sample_only and row['status']=='passed_sample'
                                 and row.get('visual',{}).get('passed')
                                 and digest(path.read_bytes())==row['image_sha256'])
            if not recovering_sample:
                raise FileExistsError('正式文件同名，拒绝覆盖')
    if adapter is None:
        from kie_adapter import KIEAdapter, config_status
        status = config_status()
        if not status['api_key_configured'] or not status['model_compatible']:
            raise ValueError('缺少 KIE 配置；未创建产物')
        adapter = KIEAdapter()
    if manifest['provider'] != adapter.name:
        raise ValueError('禁止跨 provider 继续或回退')
    root.mkdir(parents=True, exist_ok=True)
    input_copy = safe_path(root, f"输入/需求{Path(plan['source']).suffix.lower()}")
    if input_copy.exists():
        if digest(input_copy.read_bytes()) != plan['source_sha256']:
            raise ValueError('输入副本与当前需求不一致')
    else:
        input_copy.parent.mkdir(parents=True, exist_ok=True)
        with input_copy.open('xb') as handle:
            handle.write(Path(plan['source']).read_bytes())
    manifest['phase'] = 'sample' if sample_only else 'full'
    save_manifest(root, manifest)
    print(f'本次目标 {len(selected)} 张；provider={adapter.name}；每目标最多3次生成。')
    from kie_adapter import KIEAdapterError
    for target in selected:
        row = manifest['images'][target['id']]
        if row['status'] == 'passed_sample' and not sample_only:
            row['phase'] = 'full'
            promote(root, row)
            save_manifest(root, manifest)
            continue
        if row['status'] in {'passed', 'passed_sample', 'pending_review', 'blocked', 'exhausted'}:
            continue
        row['phase'] = 'sample' if sample_only else 'full'
        while len(row['attempts']) < 3 or row['status'] == 'pending_task':
            resuming = row['status'] == 'pending_task'
            if resuming:
                attempt = row['attempts'][-1]
            else:
                number = len(row['attempts'])+1
                stem = target['id'].replace(':', '-')
                attempt = {'number': number, 'raw': f'临时图/{stem}/attempt-{number}-raw.png',
                           'candidate': f"临时图/{stem}/attempt-{number}.{target['extension']}", 'status': 'submitting'}
                row['attempts'].append(attempt)
            raw_path = safe_path(root, attempt['raw'])
            candidate = safe_path(root, attempt['candidate'])
            if not resuming and (raw_path.exists() or candidate.exists()):
                raise FileExistsError('尝试文件已存在，拒绝覆盖')
            if resuming:
                physical = validate_file(candidate, target)
                if physical['passed']:
                    attempt['recovered_candidate'] = True
                    record_candidate(row, attempt, physical)
                    break
                if candidate.exists():
                    attempt.setdefault('previous_candidates', []).append(attempt['candidate'])
                    attempt['candidate'] = str(candidate.with_name(f'{candidate.stem}-recovered-{uuid4().hex}{candidate.suffix}').relative_to(root))
                    candidate = safe_path(root, attempt['candidate'])
                if raw_path.exists():
                    try:
                        attempt['processing'] = process_image(raw_path, candidate, target)
                        physical = validate_file(candidate, target)
                        if physical['passed']:
                            attempt['recovered_raw'] = True
                            record_candidate(row, attempt, physical)
                            break
                    except (ValueError, OSError):
                        pass  # Resume this same task; never infer a new paid job from an interrupted file.
                    attempt.setdefault('previous_raw', []).append(attempt['raw'])
                    attempt['raw'] = str(raw_path.with_name(f'{raw_path.stem}-resumed-{uuid4().hex}.png').relative_to(root))
                    raw_path = safe_path(root, attempt['raw'])
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            row['status'] = 'pending_task'
            save_manifest(root, manifest)
            def submitted(task_id):
                nonlocal before
                manifest['provider_submission_attempts'] += adapter.submissions-before
                before = adapter.submissions
                attempt.update(task_id=task_id, status='pending_task')
                if adapter.name == 'kie':
                    attempt['model'] = 'gpt-image-2-image-to-image' if references else 'gpt-image-2-text-to-image'
                save_manifest(root, manifest)
            references = list(styles[target['id']])
            # A generated anchor, when present, always precedes historical style.
            same = [manifest['images'][r['id']] for r in anchors if r['family']==target['family']]
            other = [manifest['images'][r['id']] for r in anchors if r['family']!=target['family']]
            master = next((r for r in same+other if r['target']['id']!=target['id'] and r.get('candidate') and r['status'] in {'pending_review','passed_sample','passed'}), None)
            if master:
                references.insert(0, safe_path(root, master['candidate']))
            before = adapter.submissions
            try:
                if resuming:
                    if not attempt.get('task_id'):
                        row.update(status='blocked', failure_reason='submission_state_unknown')
                        break
                    result = adapter.resume(task_id=attempt['task_id'], output_path=raw_path, target=target,
                                            **({'model': attempt['model']} if attempt.get('model') else {}))
                else:
                    result = adapter.generate(prompt=build_prompt(plan,target), references=references,
                                              output_path=raw_path, target=target, on_submitted=submitted)
                attempt['provider_result'] = result
                if result.get('model'):
                    attempt['model'] = result['model']
            except KIEAdapterError as exc:
                attempt.update(status='pending_task' if exc.uncertain and exc.task_id else 'blocked' if exc.uncertain else 'failed', task_id=exc.task_id)
                if exc.model:
                    attempt['model'] = exc.model
                row.update(status=attempt['status'], failure_reason='provider_state_unknown' if exc.uncertain else 'provider_task_failed')
                if exc.uncertain:
                    break
                row['status']='failed_validation'
                continue
            except Exception:
                row.update(status='blocked', failure_reason='provider_error_details_hidden')
                attempt['status']='blocked'
                break
            finally:
                manifest['provider_submission_attempts'] += adapter.submissions-before
                save_manifest(root,manifest)
            try:
                attempt['processing'] = process_image(raw_path,candidate,target)
                physical = validate_file(candidate,target)
                if not physical['passed']:
                    raise ValueError('最终物理校验失败')
                record_candidate(row, attempt, physical)
                break
            except (ValueError,OSError):
                attempt['status']='failed_validation'
                row.update(status='failed_validation', failure_reason='postprocess_or_physical_validation_failed')
            save_manifest(root,manifest)
        if len(row['attempts'])>=3 and row['status']=='failed_validation':
            row['status']='exhausted'
        save_manifest(root,manifest)
    log = safe_path(root,'处理日志/events.jsonl')
    log.parent.mkdir(parents=True,exist_ok=True)
    with log.open('a',encoding='utf-8') as handle:
        handle.write(json.dumps({'phase':'sample' if sample_only else 'full','targets':len(selected),
                                 'provider':manifest['provider'],'submission_attempts':manifest['provider_submission_attempts'],
                                 'status':manifest['status']},ensure_ascii=False)+'\n')
    return manifest


def apply_reviews(manifest_path: Path, review_file: Path) -> dict:
    root = Path(manifest_path).parent
    if root.is_symlink() or not safe_path(root, 'manifest.json').is_file():
        raise ValueError('活动 manifest 不存在或路径不安全')
    with activity_lock(root.resolve()):
        return _apply_reviews(manifest_path, review_file)


def _apply_reviews(manifest_path: Path, review_file: Path) -> dict:
    root = Path(manifest_path).parent.resolve()
    manifest = json.loads(safe_path(root,'manifest.json').read_text(encoding='utf-8'))
    report = json.loads(Path(review_file).read_text(encoding='utf-8'))
    if report.get('plan_hash') != manifest['plan_hash'] or not isinstance(report.get('images'),list) or not report['images']:
        raise ValueError('核验结果必须绑定当前 plan_hash，并包含非空 images 列表')
    keys=[review.get('target_id') for review in report['images']]
    if len(keys)!=len(set(keys)) or set(keys)-set(manifest['images']):
        raise ValueError('核验目标重复或超出当前范围')
    for review in report['images']:
        row=manifest['images'][review['target_id']]
        if 'candidate' not in row:
            raise ValueError('目标尚无最终候选图，不能核验')
        if row['status'] in {'passed','passed_sample'}:
            raise ValueError('目标已交付，不覆盖已有核验记录')
    for review in report['images']:
        row=manifest['images'][review['target_id']]
        physical=validate_file(safe_path(root,row['candidate']),row['target'])
        if not physical['passed'] or physical.get('image_sha256')!=row['image_sha256']:
            row.update(status='failed_validation',failure_reason='candidate_changed_or_invalid',physical=physical)
            continue
        visual=validate_review(row,review)
        row.update(review=review,visual=visual,status=visual['status'],failure_reason=','.join(visual['errors']))
        row['attempts'][-1]['status']=visual['status']
        if visual['passed']:
            promote(root,row)
    save_manifest(root,manifest)
    return manifest


def summary(manifest: dict) -> str:
    lines=['| 类型 | 尺寸 | 字节数 / 严格上限 | 格式 | 文字核对 | 是否通过 | 失败原因 |',
           '| --- | --- | --- | --- | --- | --- | --- |']
    for row in manifest['images'].values():
        target=row['target']; physical=row.get('physical',{})
        text='通过' if row.get('visual',{}).get('passed') else row['status']
        passed=row['status'] in {'passed','passed_sample'}
        lines.append(f"| {target['name']} | {target['width']}x{target['height']} | {physical.get('bytes','—')} / <{target['max_bytes']} | {physical.get('format',target['format'])} | {text} | {'是' if passed else '否'} | {row.get('failure_reason','')} |")
    return '\n'.join(lines)


def run_check() -> dict:
    load_specs()
    base=SKILL_ROOT/'assets/references'
    index=yaml.safe_load((base/'index.yaml').read_text(encoding='utf-8'))
    for row in index['references']:
        if digest(safe_path(base,row['path']).read_bytes())!=row['sha256']:
            raise ValueError('参考图校验失败')
    from kie_adapter import config_status
    status=config_status()
    return {**status,'reference_count':len(index['references']),'ok':status['api_key_configured'] and status['models_configured'] and status['model_compatible']}


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description='司机端端内宣传图：先预览、确认样张、再确认批量。')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--check',action='store_true')
    mode.add_argument('--dry-run',action='store_true')
    mode.add_argument('--sample-only',action='store_true')
    parser.add_argument('--confirmed',action='store_true')
    parser.add_argument('--file',type=Path)
    parser.add_argument('--types')
    parser.add_argument('--sizes')
    parser.add_argument('--review-file',type=Path)
    args=parser.parse_args(argv)
    try:
        if args.review_file and (args.check or args.dry_run or args.sample_only):
            raise ValueError('--review-file 不能与 check/dry-run/sample-only 同用')
        if args.check:
            status=run_check(); print(json.dumps(status,ensure_ascii=False)); return 0 if status['ok'] else 1
        if not args.dry_run and not args.confirmed:
            print('FAIL: 必须先 dry-run 并显式确认；未传 --confirmed 不联网、不写文件。'); return 2
        if args.file is None:
            raise ValueError('必须提供 --file 需求文件')
        plan=build_plan(args.file,types=args.types,sizes=args.sizes)
        if args.dry_run:
            print(f"计划 {len(plan['targets'])} 张；plan_hash={plan['plan_hash']}")
            print(json.dumps({key:plan['activity'][key] for key in ('reference_set','scene','composition') if key in plan['activity']},ensure_ascii=False))
            for row in plan['targets']:
                print(f"{row['id']} {row['format']} <{row['max_bytes']} bytes {row['filename']}")
                print(json.dumps(row['text'],ensure_ascii=False))
                print(json.dumps({key:row[key] for key in ('scene','composition') if key in row},ensure_ascii=False))
            return 0
        root=OUTPUT_ROOT/plan['activity']['activity_name']
        if args.review_file:
            manifest=json.loads(safe_path(root,'manifest.json').read_text(encoding='utf-8'))
            if manifest['plan_hash']!=plan['plan_hash']:
                raise ValueError('需求/范围已变化，不能写回旧核验')
            manifest=apply_reviews(root/'manifest.json',args.review_file)
        else:
            manifest=generate_batch(plan,root,sample_only=args.sample_only,confirmed=args.confirmed)
        print(summary(manifest)); print(f"manifest: {root/'manifest.json'}")
        return 0 if manifest['status'] in {'complete','samples_ready'} else 3
    except (ValueError,FileExistsError,FileNotFoundError) as exc:
        print(f'FAIL: {exc}'); return 2
    except Exception as exc:
        print(f'FAIL: {type(exc).__name__}；停止，未输出原始异常或凭据。'); return 1


if __name__ == '__main__':
    raise SystemExit(main())
