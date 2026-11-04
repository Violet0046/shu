"""上传存储与模型调用分离；原文件和模型用图分开保存。"""
import base64
import binascii
import hashlib
import io
import os
import secrets
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from .errors import ServiceError


@dataclass
class PreparedImage:
    raw: bytes
    image: Image.Image
    extension: str
    digest: str


class LocalFileStorage:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ServiceError(422, '无效的文件位置')
        return path

    def prepare(self, encoded):
        try:
            raw = base64.b64decode(encoded.split(',', 1)[-1], validate=True)
            if len(raw) > 15 * 1024 * 1024:
                raise ServiceError(413, '图片不能超过 15 MB')
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                image = Image.open(io.BytesIO(raw))
                if image.width * image.height > 30_000_000:
                    raise ValueError()
                extension = {'JPEG':'jpg','PNG':'png','WEBP':'webp','GIF':'gif','TIFF':'tiff','BMP':'bmp'}.get(image.format)
                if not extension:
                    raise ValueError()
                image.load()
                image = ImageOps.exif_transpose(image).convert('RGB')
                image.thumbnail((3072, 3072))
            return PreparedImage(raw, image, extension, hashlib.sha256(raw).hexdigest())
        except (ValueError, OSError, binascii.Error, Image.DecompressionBombWarning, Image.DecompressionBombError):
            raise ServiceError(422, '请上传有效图片，且像素不超过三千万；PDF 请先截取作文区域')

    def save(self, sid, prepared):
        model_key, source_key = f'{sid}.jpg', f'{sid}.source.{prepared.extension}'
        try:
            self.path(source_key).write_bytes(prepared.raw)
            prepared.image.save(self.path(model_key), 'JPEG', quality=92)
        except Exception:
            self.discard(model_key, source_key)
            raise
        return model_key, source_key

    def discard(self, *keys):
        for key in keys:
            self.path(key).unlink(missing_ok=True)

    def preview(self, key):
        source = self.path(key)
        target = source.with_suffix('.preview.jpg')
        if not target.exists():
            temporary = target.with_name(target.name+'.'+secrets.token_hex(8)+'.tmp')
            try:
                with Image.open(source) as image:
                    image.thumbnail((1600,1600))
                    image.convert('RGB').save(temporary,'JPEG',quality=82,optimize=True)
                os.replace(temporary,target)
            finally:
                temporary.unlink(missing_ok=True)
        return target
