"""Bounded, authorization-scoped subscriptions for registered stream sources."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager


class StreamHub:
    def __init__(self, capacity=32, retry_seconds=1):
        self.capacity, self.retry_seconds = capacity, retry_seconds
        self.channels = {}

    @asynccontextmanager
    async def subscribe(self, owner, entitlement, binding, connector):
        key = (owner, entitlement, binding)
        channel = self.channels.get(key)
        if channel is None:
            channel = {'subscribers':set(), 'last':None, 'sequence':None}
            self.channels[key] = channel
            channel['task'] = asyncio.create_task(self.pump(channel, connector))
        queue = asyncio.Queue(maxsize=self.capacity)
        channel['subscribers'].add(queue)
        if channel['last'] is not None:
            queue.put_nowait(channel['last'])
        try:
            yield queue
        finally:
            channel['subscribers'].discard(queue)
            if not channel['subscribers']:
                self.channels.pop(key, None)
                channel['task'].cancel()
                await asyncio.gather(channel['task'], return_exceptions=True)

    def publish(self, channel, event):
        channel['last'] = event
        for queue in channel['subscribers']:
            value = dict(event)
            if queue.full():
                queue.get_nowait()
                value['gap'] = True
                value['refreshRequired'] = True
            queue.put_nowait(value)

    async def pump(self, channel, connector):
        while True:
            try:
                async for event in connector(channel['sequence']):
                    if event.get('status') in ('reconnecting','unauthorized') and 'rows' not in event:
                        self.publish(channel, event)
                        continue
                    sequence = event.get('sequence')
                    if not isinstance(sequence, int) or not isinstance(event.get('rows'), list):
                        continue
                    previous = channel['sequence']
                    if previous is not None and sequence <= previous:
                        continue
                    gap = previous is not None and sequence != previous + 1
                    channel['sequence'] = sequence
                    self.publish(channel, {**event,'mode':'stream','gap':gap or event.get('gap',False),'refreshRequired':gap or event.get('refreshRequired',False)})
            except asyncio.CancelledError:
                raise
            except PermissionError:
                self.publish(channel, {'status':'unauthorized','error':'Source credential or streaming entitlement was rejected.'})
                return
            except Exception:
                pass
            self.publish(channel, {'status':'reconnecting','refreshRequired':True})
            await asyncio.sleep(self.retry_seconds)

    async def close(self):
        tasks = [channel['task'] for channel in self.channels.values()]
        self.channels.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
