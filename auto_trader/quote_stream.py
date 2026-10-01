"""One official Toss connection shared by authenticated quote viewers."""
import asyncio
import json
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus


class QuoteStream:
    def __init__(self, client, url):
        self.client, self.url = client, url
        self.listeners = {}
        self.changed = True
        self.task = None

    def publish(self, symbol, frame):
        for code, queues in list(self.listeners.items()):
            if symbol is not None and symbol != code:
                continue
            for queue in list(queues):
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(frame)

    def subscribe(self, symbol):
        if symbol not in self.listeners and len(self.listeners) >= 100:
            raise ValueError("동시 시세 구독 종목은 최대 100개입니다.")
        queue = asyncio.Queue(maxsize=1)
        self.listeners.setdefault(symbol, set()).add(queue)
        self.changed = True
        queue.put_nowait({"type": "status", "state": "connecting"})
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())
        return queue

    async def unsubscribe(self, symbol, queue):
        queues = self.listeners.get(symbol, set())
        queues.discard(queue)
        if not queues:
            self.listeners.pop(symbol, None)
        self.changed = True
        if not self.listeners:
            await self.close()

    async def close(self):
        task, self.task = self.task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def run(self):
        delay = 1
        while self.listeners:
            try:
                token = await asyncio.to_thread(self.client.access_token)
                async with connect(self.url, additional_headers={"Authorization": "Bearer " + token},
                                   open_timeout=10, ping_interval=60, ping_timeout=20,
                                   proxy=None, max_queue=16) as socket:
                    self.changed = True
                    while self.listeners:
                        if self.changed:
                            self.changed = False
                            await socket.send(json.dumps([{"type": "trade:kr", "codes": sorted(self.listeners)}]))
                        try:
                            frame = json.loads(await asyncio.wait_for(socket.recv(), 1))
                        except asyncio.TimeoutError:
                            continue
                        if frame.get("type") == "subscriptions":
                            for topic in frame.get("subscribed", []):
                                if topic.startswith("trade:kr:"):
                                    self.publish(topic.split(":")[-1], {"type": "status", "state": "connected"})
                            for rejected in frame.get("rejected", []):
                                self.publish(rejected.get("target", "").split(":")[-1], {"type": "status", "state": "rejected"})
                            delay = 1
                        elif frame.get("type") == "message" and frame.get("topic", "").startswith("trade:kr:"):
                            self.publish(frame["topic"].split(":")[-1], frame)
                        elif frame.get("type") == "error":
                            raise ValueError("Stream subscription error")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if isinstance(error, InvalidStatus) and error.response.status_code == 401:
                    self.client._invalidate_access_token(token)
                self.publish(None, {"type": "status", "state": "reconnecting"})
                await asyncio.sleep(delay)
                delay = min(30, delay * 2)
