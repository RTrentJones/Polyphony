UPDATE jobs SET status = 'cancelled' WHERE status IN ('queued', 'running');
