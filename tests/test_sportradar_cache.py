from app.api_clients.sportradar_cache import SportradarCache
from app.config import settings

def test_cache_set_get():
    cache = SportradarCache()
    # Bypass Redis for consistent mock in-memory caches
    cache.redis_client = None
    
    # Enable cache explicitly
    settings.SPORTRADAR_ENABLE_CACHE = True
    
    cache.set("/test/endpoint", "hash123", "response_content_here", ttl_seconds=10)
    
    val = cache.get("/test/endpoint", "hash123")
    assert val == "response_content_here"
    
    # Clear cache
    cache.clear()
    assert cache.get("/test/endpoint", "hash123") is None
