"""Read final bytes independently; visual assertions never default to pass."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from input_plan import digest
from postprocess import has_transparent_exterior

REVIEW_CHECKS = ('text_exact', 'amounts_dates_exact', 'no_extra_copy', 'no_crop',
                 'subject_consistent', 'layout_correct', 'transparency_correct')


def validate_file(path: Path, target: dict) -> dict:
    errors = []
    result = {'passed': False, 'errors': errors, 'text_status': 'pending_review'}
    try:
        data = Path(path).read_bytes()
        result.update(bytes=len(data), image_sha256=digest(data))
        if len(data) >= target['max_bytes']:
            errors.append('bytes_limit')
        with Image.open(path) as image:
            result.update(format=image.format, size=list(image.size), mode=image.mode)
            if image.format != target['format']:
                errors.append('format')
            if image.size != (target['width'], target['height']):
                errors.append('dimensions')
            if getattr(image, 'n_frames', 1) != 1:
                errors.append('animation')
            if image.getexif() or any(key in image.info for key in ('exif', 'xmp', 'XML:com.adobe.xmp', 'icc_profile', 'comment')) or getattr(image, 'text', {}):
                errors.append('metadata')
            if target.get('transparent') and (image.format != 'PNG' or not has_transparent_exterior(image)):
                errors.append('transparency')
            image.load()
    except (OSError, ValueError, Image.DecompressionBombError):
        errors.append('unreadable_image')
    result['passed'] = not errors
    return result


def validate_review(row: dict, review: dict | None) -> dict:
    if review is None:
        return {'passed': False, 'status': 'pending_review', 'errors': ['missing_visual_review']}
    errors = []
    if not isinstance(review, dict):
        return {'passed': False, 'status': 'failed_review', 'errors': ['invalid_review']}
    if review.get('image_sha256') != row['image_sha256']:
        errors.append('review_hash_mismatch')
    if review.get('viewed') is not True or not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
        errors.append('not_viewed')
    if review.get('observed_text') != row['target']['text']:
        errors.append('text_mismatch')
    if review.get('unexpected_text') != []:
        errors.append('unexpected_text')
    checks = review.get('checks')
    if not isinstance(checks, dict):
        checks = {}
    errors.extend(key for key in REVIEW_CHECKS if checks.get(key) is not True)
    if not isinstance(review.get('notes'), str) or not review['notes'].strip():
        errors.append('missing_review_notes')
    return {'passed': not errors, 'status': 'passed' if not errors else 'failed_review', 'errors': errors}
