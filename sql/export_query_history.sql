-- =============================================================================
-- Snowflake Cost Guard — export per-query history from ACCOUNT_USAGE
-- =============================================================================
-- Run in Snowflake (Snowsight or SnowSQL) as a role with access to
-- SNOWFLAKE.ACCOUNT_USAGE (ACCOUNTADMIN, or a role granted IMPORTED PRIVILEGES
-- on the SNOWFLAKE database). ACCOUNT_USAGE data is delayed up to ~3 hours.
--
-- The column list below is exactly the CSV contract snowflake-cost-guard
-- expects. Export the result as CSV with headers and feed it to:
--   python -m snowflake_cost_guard --input query_history.csv --output report.html
--
-- Tip: change the lookback window to suit your bill — 30 to 90 days is typical.
-- =============================================================================

SELECT
    query_id,
    query_text,
    user_name,
    warehouse_name,
    warehouse_size,
    start_time,
    execution_time                              AS execution_time_ms,
    bytes_scanned,
    partitions_scanned,
    partitions_total,
    bytes_spilled_to_remote_disk                AS bytes_spilled_remote,
    rows_produced
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE start_time >= DATEADD(day, -30, CURRENT_TIMESTAMP)
  AND warehouse_name IS NOT NULL          -- cached / cloud-services-only queries burn no warehouse credits
  AND execution_status = 'SUCCESS'
ORDER BY start_time;
