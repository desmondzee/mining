import asyncio


class FrameHub:
    def __init__(self) -> None:
        self._frames: dict[str, tuple[int, bytes]] = {}
        self._seq = 0
        self._cond = asyncio.Condition()

    async def publish(self, camera: str, jpeg: bytes) -> None:
        async with self._cond:
            self._seq += 1
            self._frames[camera] = (self._seq, jpeg)
            self._cond.notify_all()

    def _newer(self, seen: dict[str, int]) -> bool:
        return any(seq > seen.get(cam, 0) for cam, (seq, _) in self._frames.items())

    async def wait_newer(self, seen: dict[str, int]) -> list[tuple[str, bytes]]:
        async with self._cond:
            await self._cond.wait_for(lambda: self._newer(seen))
            out = []
            for cam, (seq, jpeg) in self._frames.items():
                if seq > seen.get(cam, 0):
                    seen[cam] = seq
                    out.append((cam, jpeg))
            return out
