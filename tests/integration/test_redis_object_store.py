# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest
from nat.data_models.object_store import NoSuchKeyError
from nat.object_store.models import ObjectStoreItem

from nat.plugins.redis.redis_object_store import RedisObjectStore


async def test_redis_object_store_roundtrip(local_redis_params: tuple[str, int], unique_suffix: str) -> None:
    host, port = local_redis_params

    key = f"redis_os_{unique_suffix}"
    item = ObjectStoreItem(data=b"hello", content_type="application/octet-stream", metadata={"k": "v"})

    store = RedisObjectStore(
        bucket_name=f"test_bucket_{unique_suffix}",
        host=host,
        port=port,
        db=0,
    )
    async with store:
        await store.put_object(key, item)
        loaded = await store.get_object(key)
        assert loaded.data == item.data
        assert loaded.content_type == item.content_type
        assert loaded.metadata == item.metadata

        await store.delete_object(key)
        with pytest.raises(NoSuchKeyError):
            await store.get_object(key)
