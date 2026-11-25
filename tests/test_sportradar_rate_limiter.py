import time
from app.api_clients.sportradar_rate_limiter import SportradarRateLimiter

def test_rate_limiter_delay():
    limiter = SportradarRateLimiter()
    # Bypass Redis for rate limit test consistency (force fallback in-memory limiter)
    limiter.redis_client = None
    
    # First acquire should be instant
    t0 = time.time()
    limiter.acquire("limiter_key_1")
    t1 = time.time()
    assert (t1 - t0) < 0.1
    
    # Second acquire should wait at least ~0.95 seconds if QPS is 1
    limiter.acquire("limiter_key_1")
    t2 = time.time()
    elapsed = t2 - t1
    assert elapsed >= 0.8
