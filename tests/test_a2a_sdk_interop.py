from __future__ import annotations

import asyncio
import unittest

try:
    from a2a.client import ClientFactory
    from a2a.client.card_resolver import A2ACardResolver
    from a2a.client.client import ClientConfig
    from a2a.types import DataPart, Message, Part, Role, Task, TaskQueryParams, TransportProtocol
    import httpx
    A2A_SDK_AVAILABLE = True
except ImportError:
    A2A_SDK_AVAILABLE = False

from opspilot.a2a import A2AServer


@unittest.skipUnless(A2A_SDK_AVAILABLE, "install requirements-a2a-interop.txt")
class OfficialSdkInteropTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_sdk_client_discovers_and_uses_local_agent(self):
        def delayed_handler(payload):
            import time
            time.sleep(0.05)
            return {"accepted": payload.get("probe")}

        with A2AServer(delayed_handler) as server:
            async with httpx.AsyncClient(timeout=3) as http_client:
                card = await A2ACardResolver(http_client, server.url.rstrip("/")).get_agent_card()
                client = ClientFactory(ClientConfig(
                    streaming=False,
                    polling=False,
                    httpx_client=http_client,
                    supported_transports=[TransportProtocol.jsonrpc],
                )).create(card)
                message = Message(
                    role=Role.user,
                    messageId="official-sdk-interop-001",
                    parts=[Part(DataPart(data={"task_id": "interop", "probe": "round-trip"}))],
                )
                responses = [event async for event in client.send_message(message)]
                task = next((event[0] if isinstance(event, tuple) else event
                             for event in responses
                             if isinstance(event, Task) or isinstance(event, tuple) and isinstance(event[0], Task)), None)
                self.assertIsNotNone(task)
                task = await client.get_task(TaskQueryParams(id=task.id))
                for _ in range(40):
                    if task is None or task.status.state.value not in {"working", "submitted", "input-required"}:
                        break
                    task = await client.get_task(TaskQueryParams(id=task.id))
                    await asyncio.sleep(0.01)
                self.assertIsNotNone(task)
                self.assertEqual("completed", task.status.state.value)
                artifact_data = task.artifacts[0].parts[0].root.data
                self.assertEqual("round-trip", artifact_data["accepted"])
                await client.close()


if __name__ == "__main__":
    unittest.main()
