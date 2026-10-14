
import os
import mysql.connector
import numpy as np
from dotenv import load_dotenv

load_dotenv()

db = None
cursor = None

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "inventory_db")
    )

    cursor = db.cursor()

    cursor.execute("""
        SELECT name, quantity, price, min_stock
        FROM products
    """)

    rows = cursor.fetchall()

    if not rows:
        print("No inventory data available.")
    else:
        names = [row[0] for row in rows]
        quantities = np.array(
            [row[1] for row in rows], dtype=float
        )
        prices = np.array(
            [float(row[2]) for row in rows], dtype=float
        )
        minimums = np.array(
            [row[3] for row in rows], dtype=int
        )

        stock_values = quantities * prices
        low_stock = quantities <= minimums

        print("\nINVENTORY ANALYSIS")
        print("------------------")
        print("Total Products:", len(names))
        print("Total Units:", int(np.sum(quantities)))
        print(
            "Total Stock Value:",
            round(float(np.sum(stock_values)), 2)
        )
        print(
            "Average Quantity:",
            round(float(np.mean(quantities)), 2)
        )
        print("Low Stock Products:", int(np.sum(low_stock)))

        print("\nProducts Requiring Restocking:")
        for name, quantity in zip(
            np.array(names)[low_stock],
            quantities[low_stock]
        ):
            print(f"{name}: {int(quantity)} units")

except mysql.connector.Error as error:
    print("Database error:", error)

finally:
    if cursor:
        cursor.close()
    if db and db.is_connected():
        db.close()
