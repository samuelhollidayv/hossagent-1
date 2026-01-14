import os
import snowflake.connector

conn = snowflake.connector.connect(
    account=os.getenv("SNOWFLAKE_ACCOUNT"),
    user=os.getenv("SNOWFLAKE_USER"),
    role=os.getenv("SNOWFLAKE_ROLE"),
    warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
    database=os.getenv("SNOWFLAKE_DATABASE"),
    schema=os.getenv("SNOWFLAKE_SCHEMA"),
    authenticator="externalbrowser",
)

cur = conn.cursor()

cur.execute("""
    SELECT column_name
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE table_schema = 'BT_HCA_HCDM'
      AND table_name = 'RXP_PRESCRIPTIONS'
    ORDER BY ordinal_position
""")

print("\n=== RXP_PRESCRIPTIONS columns ===\n")
for r in cur.fetchall():
    print(r[0])

cur.close()
conn.close()
