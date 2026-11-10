"""只压缩接口 JSON 和页面 / 脚本 / 样式;图片、下载包本来就压缩过,再压只是白费 CPU。"""
from starlette.middleware.gzip import GZipMiddleware

SKIP = ("/images/", "/note_images/", "/product_images/", "/download/")


class TextGZip(GZipMiddleware):
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(SKIP):
            return await self.app(scope, receive, send)
        await super().__call__(scope, receive, send)
