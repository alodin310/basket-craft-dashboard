import os
import datetime
import altair as alt
import pandas as pd
import streamlit as st
import snowflake.connector
from dotenv import load_dotenv

load_dotenv()

@st.cache_resource
def get_connection():
    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        role=os.getenv("SNOWFLAKE_ROLE"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA"),
    )

@st.cache_data
def get_kpis():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        WITH monthly AS (
            SELECT
                YEAR(order_date)                           AS yr,
                MONTH(order_date)                          AS mo,
                SUM(line_total)                            AS revenue,
                COUNT(DISTINCT order_id)                   AS orders,
                SUM(line_total) / COUNT(DISTINCT order_id) AS aov,
                COUNT(order_item_id)                       AS items_sold
            FROM fct_order_items
            GROUP BY 1, 2
            ORDER BY 1 DESC, 2 DESC
            LIMIT 2
        )
        SELECT revenue, orders, aov, items_sold FROM monthly
    """)
    return cur.fetchall()

@st.cache_data
def get_date_bounds():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT MIN(order_date), MAX(order_date) FROM fct_order_items")
    return cur.fetchone()

@st.cache_data
def get_products():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT product_id, product_name FROM dim_products ORDER BY product_name")
    return cur.fetchall()

@st.cache_data
def get_bundle(product_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            p.product_name      AS also_bought,
            COUNT(DISTINCT a.order_id) AS orders_together
        FROM fct_order_items a
        JOIN fct_order_items b
            ON  a.order_id   = b.order_id
            AND a.product_id != b.product_id
        JOIN dim_products p ON b.product_id = p.product_id
        WHERE a.product_id = %(pid)s
        GROUP BY 1
        ORDER BY 2 DESC
    """, {"pid": product_id})
    rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["Also Bought", "# of Orders"])

@st.cache_data
def get_top_products(start_date: str, end_date: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            p.product_name,
            SUM(f.line_total) AS revenue
        FROM fct_order_items f
        JOIN dim_products p ON f.product_id = p.product_id
        WHERE f.order_date BETWEEN %(start)s AND %(end)s
        GROUP BY 1
        ORDER BY 2 DESC
    """, {"start": start_date, "end": end_date})
    rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["product", "revenue"])

@st.cache_data
def get_revenue_trend(start_date: str, end_date: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            TO_CHAR(order_date, 'YYYY-MM') AS month,
            SUM(line_total)                AS revenue
        FROM fct_order_items
        WHERE order_date BETWEEN %(start)s AND %(end)s
        GROUP BY 1
        ORDER BY 1
    """, {"start": start_date, "end": end_date})
    rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["month", "revenue"])

def pct_delta(current, prior):
    if prior and prior != 0:
        return (current - prior) / prior
    return 0.0

st.title("Basket Craft — Merchandising Dashboard")

# KPIs — always show latest two months, unaffected by date filter
rows = get_kpis()
if len(rows) >= 2:
    curr, prev = rows[0], rows[1]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Revenue",    f"${curr[0]:,.0f}", f"{pct_delta(curr[0], prev[0]):.1%}")
    col2.metric("Orders",     f"{curr[1]:,}",     f"{pct_delta(curr[1], prev[1]):.1%}")
    col3.metric("AOV",        f"${curr[2]:,.2f}", f"{pct_delta(curr[2], prev[2]):.1%}")
    col4.metric("Items Sold", f"{curr[3]:,}",     f"{pct_delta(curr[3], prev[3]):.1%}")

# Sidebar date filter
min_date, max_date = get_date_bounds()
st.sidebar.header("Filters")
start_date = st.sidebar.date_input("Start date", value=min_date, min_value=min_date, max_value=max_date)
end_date   = st.sidebar.date_input("End date",   value=max_date, min_value=min_date, max_value=max_date)

# Revenue trend
st.subheader("Revenue Trend")
trend_df = get_revenue_trend(str(start_date), str(end_date))
st.line_chart(trend_df.set_index("month")["revenue"])

# Top products
st.subheader("Top Products by Revenue")
products_df = get_top_products(str(start_date), str(end_date))
sorted_products = products_df.sort_values("revenue", ascending=False)
chart = alt.Chart(sorted_products).mark_bar().encode(
    x=alt.X("product:N", sort=list(sorted_products["product"]), title="Product"),
    y=alt.Y("revenue:Q", title="Revenue ($)"),
)
st.altair_chart(chart, use_container_width=True)

# Bundle finder
st.subheader("Bundle Finder: Bought With…")
products = get_products()
product_map = {name: pid for pid, name in products}
selected = st.selectbox("Pick a product", list(product_map.keys()))
bundle_df = get_bundle(product_map[selected])
st.dataframe(bundle_df, use_container_width=True, hide_index=True)
st.download_button("Download CSV", bundle_df.to_csv(index=False), "bundle.csv", "text/csv")
