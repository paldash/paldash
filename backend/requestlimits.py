"""Bound request bytes before JSON parsing, including chunked requests."""
import asyncio
import json


class RequestLimits:
    def __init__(self, app, upload_limit=64 * 1024 * 1024):
        self.app = app
        self.upload_limit = upload_limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PATCH', 'PUT'):
            return await self.app(scope, receive, send)
        path = scope.get('path', '')
        limit = self.upload_limit if path in ('/api/export/verify', '/api/import/preview', '/api/import/apply') else 1024 * 1024
        if path.startswith('/api/auth/'):
            limit = 8192
        chunks = []
        total = 0
        try:
            async with asyncio.timeout(30):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    body = message.get('body', b'')
                    total += len(body)
                    if total > limit:
                        await self.reject(send, 413, 'Request body is too large')
                        return
                    chunks.append(body)
                    if not message.get('more_body', False):
                        break
        except TimeoutError:
            await self.reject(send, 408, 'Request body timed out')
            return
        consumed = False
        async def buffered_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {'type': 'http.request', 'body': b''.join(chunks), 'more_body': False}
            return await receive()
        return await self.app(scope, buffered_receive, send)

    @staticmethod
    async def reject(send, status, detail):
        body = json.dumps({'detail': detail}).encode()
        await send({'type': 'http.response.start', 'status': status, 'headers': [(b'content-type', b'application/json')]})
        await send({'type': 'http.response.body', 'body': body})
