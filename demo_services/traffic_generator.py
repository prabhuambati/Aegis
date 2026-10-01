import asyncio
import os
import httpx

TARGET = os.getenv("TARGET_URL", "http://demo-payment:8004/checkout")


async def main():
    async with httpx.AsyncClient(timeout=3) as client:
        for index in range(12):
            headers = {"x-request-id": f"live-demo-{index:03d}"}
            try:
                response = await client.get(TARGET, headers=headers)
                print({"request": index, "status": response.status_code}, flush=True)
            except Exception as exc:
                print({"request": index, "error": type(exc).__name__}, flush=True)
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
