"""Quick WebSocket diagnostic — sends one message, prints all events."""
import asyncio
import json
import websockets


async def test():
    async with websockets.connect("ws://127.0.0.1:8126/ws/diag1") as ws:
        await ws.send(json.dumps({"message": "what is SEC?"}))
        events = []
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=90)
                d = json.loads(msg)
                events.append(d)
                t = d.get("type")
                if t == "trace":
                    cat = d.get("cat", "")
                    act = d.get("action", "")
                    txt = d.get("text", "")[:200]
                    if act == "debug":
                        print(f"  ** DEBUG: {txt}")
                    else:
                        print(f"  TRACE: {cat}/{act} | {txt}")
                elif t == "stream":
                    pass  # skip individual tokens
                elif t == "response":
                    content = d.get("content", "")
                    print(f"  RESPONSE: {len(content)} chars")
                    print(f"  first 300: {content[:300]}")
                    break
                elif t == "error":
                    print(f"  ERROR: {d.get('content')}")
                    break
                else:
                    print(f"  OTHER: {t}")
            except asyncio.TimeoutError:
                print("TIMEOUT waiting for events")
                break

        stream_count = sum(1 for e in events if e.get("type") == "stream")
        print(f"\nTotal events: {len(events)}, stream tokens: {stream_count}")


asyncio.run(test())
