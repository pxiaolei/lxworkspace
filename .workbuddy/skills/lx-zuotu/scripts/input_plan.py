"""Offline activity parsing and configuration-driven image planning."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_ROOT.parents[2]
SPECS_PATH = SKILL_ROOT / 'specs.yaml'
FIELDS = ('subtitle', 'title', 'selling_points', 'amounts', 'dates', 'rules', 'button')
ALIASES = dict(zip(
    ('品牌', '活动名', '参考风格', '图片类型', '分类型文案', '副标题', '标题', '卖点', '金额', '日期', '规则小字', '按钮文案'),
    ('brand', 'activity_name', 'reference_set', 'types', 'type_text', *FIELDS),
))


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if any(not isinstance(key, str) for key in keys) or len(keys) != len(set(keys)):
            raise ValueError('字段键必须是文本且不得重复')
        return super().construct_mapping(node, deep=deep)


def read_yaml(path: Path):
    try:
        return yaml.load(path.read_text(encoding='utf-8-sig'), Loader=UniqueLoader)
    except yaml.YAMLError:
        raise ValueError('YAML/TXT 字段格式错误，请对照 runtime.md') from None


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_hash(data: object) -> str:
    return digest(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())


def safe_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError('活动名/规格名必须是非空文本且不能含首尾空格')
    if len(value) > 100 or re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.endswith('.') or value in {'.', '..'}:
        raise ValueError('活动名/规格名含不安全路径字符或长度超限')
    if value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        raise ValueError('活动名/规格名不能使用 Windows 保留名称')
    return value


def normalize_keys(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError('需求文件必须是字段映射，TXT 也使用文档化的字段格式')
    result = {}
    for key, value in data.items():
        name = ALIASES.get(key, key)
        if name in result:
            raise ValueError('同一字段不能重复声明')
        result[name] = value
    return result


def text_lines(value: object) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    if any(not isinstance(item, str) for item in values):
        raise ValueError('文案、金额和日期必须写成文本；纯数字请加引号')
    return [line for item in values for line in item.splitlines() if line.strip()]


def load_activity(path: Path) -> dict:
    if path.suffix.lower() not in {'.txt', '.yaml', '.yml'}:
        raise ValueError('需求文件只支持 TXT/YAML')
    activity = normalize_keys(read_yaml(path))
    allowed = {'brand', 'activity_name', 'reference_set', 'types', 'type_text', *FIELDS}
    if set(activity) - allowed:
        raise ValueError('需求包含未定义字段，请对照 runtime.md')
    safe_name(activity.get('activity_name', ''))
    if not isinstance(activity.get('brand'), str) or not activity['brand'].strip():
        raise ValueError('品牌必填')
    for field in FIELDS:
        if field in activity:
            activity[field] = text_lines(activity[field])
    activity.setdefault('types', 'all')
    activity.setdefault('reference_set', 'mayday')
    safe_name(activity['reference_set'])
    overrides = activity.get('type_text') or {}
    if not isinstance(overrides, dict):
        raise ValueError('分类型文案必须是映射')
    activity['type_text'] = {}
    for kind, raw in overrides.items():
        value = normalize_keys(raw)
        if set(value) - set(FIELDS):
            raise ValueError('分类型文案含未定义字段')
        activity['type_text'][kind] = {key: text_lines(item) for key, item in value.items()}
    return activity


def load_specs(path: Path = SPECS_PATH) -> dict:
    data = read_yaml(path)
    if not isinstance(data, dict) or not isinstance(data.get('types'), dict) or not data['types']:
        raise ValueError('specs.yaml 缺少 types')
    for key, spec in data['types'].items():
        if not isinstance(key, str) or not re.fullmatch('[a-z][a-z0-9_]*', key):
            raise ValueError('规格类型 key 无效')
        safe_name(spec['name'])
        if spec['format'] not in {'JPEG', 'PNG'} or spec['extension'] != {'JPEG': 'jpg', 'PNG': 'png'}[spec['format']]:
            raise ValueError('规格格式与扩展名不一致')
        if type(spec['max_kb']) is not int or spec['max_kb'] <= 0 or not spec['sizes']:
            raise ValueError('规格上限与尺寸列表必须有效')
        if spec['family'] not in {'vertical', 'horizontal'} or spec['layout'] not in {'banner', 'splash', 'popup'}:
            raise ValueError('规格 family/layout 无效')
        if set(spec['display_fields']) - set(FIELDS):
            raise ValueError('规格展示字段无效')
        seen = set()
        for raw in spec['sizes']:
            if not isinstance(raw, dict) and (not isinstance(raw, list) or len(raw) != 2):
                raise ValueError('尺寸必须是宽高对或映射')
            size = raw if isinstance(raw, dict) else dict(zip(('width','height'),raw))
            width,height = size.get('width'),size.get('height')
            if type(width) is not int or type(height) is not int or min(width,height) <= 0 or (width,height) in seen:
                raise ValueError('规格宽高必须是正整数且不得重复')
            seen.add((width,height))
            ratio = size.get('generation_ratio',spec.get('generation_ratio'))
            resolution = size.get('resolution',spec.get('resolution'))
            allowed={'1:1','3:2','2:3','4:3','3:4','16:9','9:16','2:1','1:2','3:1','1:3','21:9','9:21','5:4','4:5'}
            if ratio not in allowed or resolution not in {'1K','2K','4K'}:
                raise ValueError('生成比例或分辨率不符合已核对的 KIE 协议')
            if resolution != '1K' and (ratio in {'5:4','4:5','3:1','1:3','9:21'} or spec.get('transparent')):
                raise ValueError('此比例或透明背景只能使用 1K')
    return data


def csv_values(raw: object) -> list[str]:
    if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
        raw = ','.join(raw)
    if not isinstance(raw, str):
        raise ValueError('类型/尺寸筛选须使用逗号分隔文本')
    values = list(dict.fromkeys(x.strip().lower() for x in raw.split(',') if x.strip()))
    if not values:
        raise ValueError('筛选不能为空')
    return values


def build_plan(file: Path, *, types: str | None = None, sizes: str | None = None, specs_path: Path = SPECS_PATH) -> dict:
    file, specs_path = Path(file).resolve(), Path(specs_path).resolve()
    activity = load_activity(file)
    specs = load_specs(specs_path)['types']
    selected = csv_values(types if types is not None else activity['types'])
    if selected == ['all']:
        selected = list(specs)
    if set(selected) - set(specs) or set(activity['type_text']) - set(specs):
        raise ValueError('存在未知图片类型')
    wanted = csv_values(sizes) if sizes is not None else None
    targets, known_sizes, seen = [], set(), set()
    for kind, spec in specs.items():
        for raw in spec['sizes']:
            size = raw if isinstance(raw, dict) else dict(zip(('width', 'height'), raw))
            width, height = size['width'], size['height']
            if type(width) is not int or type(height) is not int or min(width, height) <= 0:
                raise ValueError('规格宽高必须是正整数')
            dimensions = f'{width}x{height}'
            target_id = f'{kind}:{dimensions}'
            if target_id in seen:
                raise ValueError('规格尺寸重复')
            seen.add(target_id)
            known_sizes.add(dimensions)
            if kind not in selected or wanted is not None and dimensions not in wanted:
                continue
            values = {**activity, **activity['type_text'].get(kind, {})}
            text = {key: values[key] for key in spec['display_fields'] if values.get(key)}
            if not text:
                raise ValueError('所选图片没有可展示文案')
            targets.append({
                **{key: value for key, value in spec.items() if key not in {'sizes', 'display_fields', 'max_kb'}},
                **size, 'type': kind, 'id': target_id, 'max_bytes': spec['max_kb'] * 1024,
                'filename': f"{spec['name']}-{dimensions}-{activity['activity_name']}.{spec['extension']}",
                'text': text,
            })
    if wanted is not None and set(wanted) - known_sizes:
        raise ValueError('存在未知图片尺寸')
    if not targets:
        raise ValueError('类型与尺寸筛选交集为空')
    return {'activity': activity, 'targets': targets, 'source': str(file),
            'source_sha256': digest(file.read_bytes()), 'specs_sha256': digest(specs_path.read_bytes()),
            'plan_hash': canonical_hash({'activity': activity, 'specs': specs, 'scope': [r['id'] for r in targets]})}


def sample_targets(plan: dict) -> list[dict]:
    picked = {}
    for target in plan['targets']:
        family = target['family']
        rank = (target['sample_priority'], not target.get('sample', False))
        previous = picked.get(family)
        if previous is None or rank < (previous['sample_priority'], not previous.get('sample', False)):
            picked[family] = target
    return list(picked.values())
