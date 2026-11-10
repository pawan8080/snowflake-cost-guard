-- =============================================================================
-- Snowflake Cost Guard — warehouse metering cross-check
-- =============================================================================
-- WAREHOUSE_METERING_HISTORY is Snowflake's own hourly credit metering.
-- Use it to validate this tool's per-query estimates: the SUM of estimated
-- credits for a warehouse/hour should land close to CREDITS_USED_COMPUTE for
-- the same warehouse/hour. A gap is expected — metering includes idle time
-- (billed in 60-second minimum increments on resume) that per-query
-- execution time does not, and CREDITS_USED_CLOUD_SERVICES is separate.
-- =============================================================================

SELECT
    start_time,
    warehouse_name,
    SUM(credits_used)              AS credits_used_total,
    SUM(credits_used_compute)      AS credits_used_compute,
    SUM(credits_used_cloud_services) AS credits_used_cloud_services
FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
WHERE start_time >= DATEADD(day, -30, CURRENT_TIMESTAMP)
GROUP BY start_time, warehouse_name
ORDER BY start_time, warehouse_name;

-- Daily credit burn by warehouse (for a quick eyeball against the report):
SELECT
    DATE_TRUNC('day', start_time)  AS day,
    warehouse_name,
    SUM(credits_used_compute)      AS compute_credits
FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
WHERE start_time >= DATEADD(day, -30, CURRENT_TIMESTAMP)
GROUP BY 1, 2
ORDER BY 1, 3 DESC;
