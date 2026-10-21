"""Exact geometry and bounded encoding; never draw or replace content."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps


def has_transparent_exterior(image: Image.Image) -> bool:
    rgba = image.convert('RGBA')
    alpha = rgba.getchannel('A')
    width, height = rgba.size
    return (alpha.getextrema() == (0, 255)
            and all(alpha.getpixel(pos) == 0 for pos in ((0, 0), (width-1, 0), (0, height-1), (width-1, height-1))))


def process_image(source: Path, output: Path, target: dict) -> dict:
    source, output = Path(source), Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError('拒绝覆盖已有候选文件')
    with Image.open(source) as original:
        if getattr(original, 'n_frames', 1) != 1:
            raise ValueError('生成结果必须是静态图')
        image = ImageOps.exif_transpose(original).convert('RGBA')
    raw_size = list(image.size)
    width, height = target['width'], target['height']
    scale = max(width/image.width, height/image.height)
    crop_width, crop_height = width/scale, height/scale
    left, top = (image.width-crop_width)/2, (image.height-crop_height)/2
    crop_box = [left, top, left+crop_width, top+crop_height]
    image = ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS)
    image = Image.frombytes('RGBA', image.size, image.tobytes())
    limit = target['max_bytes']
    result = {'raw_size': raw_size, 'crop_box': crop_box}
    encoded = None
    if target['format'] == 'JPEG':
        # An opaque JPEG must not silently flatten a transparent model result.
        if image.getchannel('A').getextrema() != (255, 255):
            raise ValueError('JPG 生成结果应为满版不透明场景')
        for quality in range(95, 59, -5):
            buffer = BytesIO()
            image.convert('RGB').save(buffer, format='JPEG', quality=quality, optimize=True, progressive=True)
            if buffer.tell() < limit:
                encoded = buffer.getvalue()
                result['quality'] = quality
                break
    elif target['format'] == 'PNG':
        if target.get('transparent') and not has_transparent_exterior(image):
            raise ValueError('弹窗缺少真实透明外沿；不能把白底或棋盘格当透明')
        for colors in (256, 128, 64, 32, 16):
            quantized = image.quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
            if target.get('transparent') and not has_transparent_exterior(quantized):
                continue
            buffer = BytesIO()
            quantized.save(buffer, format='PNG', optimize=True, compress_level=9)
            if buffer.tell() < limit:
                encoded = buffer.getvalue()
                result['palette_colors'] = colors
                break
    else:
        raise ValueError('未知输出格式')
    if encoded is None:
        raise ValueError('压缩失败：保持精确尺寸仍无法严格低于上限，禁止降低分辨率或 JPG 质量低于60')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as handle:
        handle.write(encoded)
    return {**result, 'bytes': len(encoded)}
