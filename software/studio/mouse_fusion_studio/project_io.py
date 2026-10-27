"""Portable versioned JSON projects with embedded, validated image resources."""
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
from PIL import Image, ImageOps
from .model import Project, ReferenceImage

MAX_IMAGE_BYTES = 12*1024*1024
MAX_PROJECT_BYTES = 32*1024*1024


def decode_image(image):
    try:
        raw = base64.b64decode(image.data,validate=True)
        if len(raw) > MAX_IMAGE_BYTES: raise ValueError('参考图片最大 12MiB')
        with Image.open(BytesIO(raw)) as opened:
            if opened.format not in ('PNG','JPEG'): raise ValueError('仅支持 PNG/JPG/JPEG')
            if opened.width > 8192 or opened.height > 8192 or opened.width*opened.height > 20_000_000:
                raise ValueError('图片尺寸过大，最多 8192 边长和 2000 万像素')
            result = ImageOps.exif_transpose(opened).convert('RGBA')
            result.load()
        if result.size != (image.width,image.height): raise ValueError('底图资源尺寸与工程元数据不一致')
        return result
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise ValueError(f'图片资源无效：{error}') from error


def import_image(path):
    path = Path(path)
    if path.suffix.lower() not in ('.png','.jpg','.jpeg'): raise ValueError('仅支持 PNG/JPG/JPEG')
    if path.stat().st_size > MAX_IMAGE_BYTES: raise ValueError('参考图片最大 12MiB')
    raw = path.read_bytes()
    try:
        with Image.open(BytesIO(raw)) as opened:
            w,h = ImageOps.exif_transpose(opened).size
        image = ReferenceImage(name=path.name,data=base64.b64encode(raw).decode('ascii'),width=w,height=h)
        decode_image(image)
        return image
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise ValueError(f'无法导入图片：{error}') from error


def atomic_json(path, data):
    path = Path(path)
    payload = json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')
    if len(payload) > MAX_PROJECT_BYTES: raise ValueError('工程/导出文件最大 32MiB')
    path.parent.mkdir(parents=True,exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix=path.name+'.',suffix='.tmp',delete=False) as target:
            temp_name = target.name
            target.write(payload); target.flush(); os.fsync(target.fileno())
        os.replace(temp_name,path)
    finally:
        if temp_name and Path(temp_name).exists(): Path(temp_name).unlink()


def save_project(project, path):
    project.validate()
    for mode in project.modes:
        if mode.image: decode_image(mode.image)
    atomic_json(path,project.to_dict())


def load_project(path):
    path = Path(path)
    if path.stat().st_size > MAX_PROJECT_BYTES: raise ValueError('工程文件最大 32MiB')
    try:
        project = Project.from_dict(json.loads(path.read_text(encoding='utf-8')))
        for mode in project.modes:
            if mode.image: decode_image(mode.image)
        return project
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError(f'无法打开工程：{error}') from error
