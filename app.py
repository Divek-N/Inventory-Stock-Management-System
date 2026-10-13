
import os
from io import BytesIO

import mysql.connector
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file

load_dotenv()
app = Flask(__name__)


def get_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME", "inventory_db")
    )


@app.route("/")
def home():
    return render_template("index.html")


# Get products with search and category filtering
@app.route("/api/products", methods=["GET"])
def products():
    db = get_db()
    cur = db.cursor(dictionary=True)
    try:
        search = request.args.get("search", "").strip()
        category = request.args.get("category", "").strip()

        cur.execute("""
            SELECT id, name, category, price, quantity, min_stock
            FROM products
            WHERE name LIKE %s AND category LIKE %s
            ORDER BY id DESC
        """, (f"%{search}%", f"%{category}%"))

        return jsonify(cur.fetchall())
    finally:
        cur.close()
        db.close()


# Add a new product
@app.route("/api/products", methods=["POST"])
def add_product():
    data = request.get_json(silent=True) or {}
    db = None
    cur = None

    try:
        name = str(data.get("name", "")).strip()
        category = str(data.get("category", "")).strip()
        price = float(data.get("price", -1))
        quantity = int(data.get("quantity", -1))
        min_stock = int(data.get("min_stock", 5))

        if (
            not name or not category
            or not np.isfinite(price)
            or price < 0 or quantity < 0 or min_stock < 0
        ):
            return jsonify(error="Invalid product details"), 400

        db = get_db()
        cur = db.cursor()

        cur.execute("""
            INSERT INTO products
                (name, category, price, quantity, min_stock)
            VALUES (%s, %s, %s, %s, %s)
        """, (name, category, price, quantity, min_stock))

        product_id = cur.lastrowid

        if quantity > 0:
            cur.execute("""
                INSERT INTO stock_transactions
                    (product_id, transaction_type, quantity)
                VALUES (%s, 'IN', %s)
            """, (product_id, quantity))

        db.commit()
        return jsonify(message="Product added successfully"), 201

    except (ValueError, TypeError):
        if db:
            db.rollback()
        return jsonify(error="Enter valid values"), 400
    finally:
        if cur:
            cur.close()
        if db:
            db.close()


# Update a product
@app.route("/api/products/<int:pid>", methods=["PUT"])
def update_product(pid):
    data = request.get_json(silent=True) or {}
    db = None
    cur = None

    try:
        name = str(data.get("name", "")).strip()
        category = str(data.get("category", "")).strip()
        price = float(data.get("price", -1))
        min_stock = int(data.get("min_stock", 5))

        if (
            not name or not category
            or not np.isfinite(price)
            or price < 0 or min_stock < 0
        ):
            return jsonify(error="Invalid product details"), 400

        db = get_db()
        cur = db.cursor()

        cur.execute("""
            UPDATE products
            SET name=%s, category=%s, price=%s, min_stock=%s
            WHERE id=%s
        """, (name, category, price, min_stock, pid))

        if cur.rowcount == 0:
            cur.execute("SELECT id FROM products WHERE id=%s", (pid,))
            exists = cur.fetchone()
            if not exists:
                return jsonify(error="Product not found"), 404
            return jsonify(message="No changes made")

        db.commit()
        return jsonify(message="Product updated successfully")

    except (ValueError, TypeError):
        if db:
            db.rollback()
        return jsonify(error="Enter valid values"), 400
    finally:
        if cur:
            cur.close()
        if db:
            db.close()


# Delete a product and its transactions
@app.route("/api/products/<int:pid>", methods=["DELETE"])
def delete_product(pid):
    db = get_db()
    cur = db.cursor()

    try:
        cur.execute("SELECT id FROM products WHERE id=%s", (pid,))
        if not cur.fetchone():
            return jsonify(error="Product not found"), 404

        cur.execute(
            "DELETE FROM stock_transactions WHERE product_id=%s",
            (pid,)
        )
        cur.execute("DELETE FROM products WHERE id=%s", (pid,))
        db.commit()

        return jsonify(message="Product deleted successfully")
    finally:
        cur.close()
        db.close()


# Record stock IN or OUT
@app.route("/api/stock", methods=["POST"])
def stock_movement():
    data = request.get_json(silent=True) or {}
    db = None
    cur = None

    try:
        product_id = int(data.get("product_id", 0))
        quantity = int(data.get("quantity", 0))
        movement_type = str(data.get("type", "")).upper()

        if (
            product_id <= 0 or quantity <= 0
            or movement_type not in ("IN", "OUT")
        ):
            return jsonify(error="Invalid stock movement"), 400

        db = get_db()
        db.start_transaction()
        cur = db.cursor()

        cur.execute("""
            SELECT quantity FROM products
            WHERE id=%s FOR UPDATE
        """, (product_id,))

        row = cur.fetchone()

        if not row:
            db.rollback()
            return jsonify(error="Product not found"), 404

        current_quantity = row[0]

        if movement_type == "OUT" and quantity > current_quantity:
            db.rollback()
            return jsonify(error="Insufficient stock"), 400

        change = quantity if movement_type == "IN" else -quantity

        cur.execute("""
            UPDATE products
            SET quantity = quantity + %s
            WHERE id=%s
        """, (change, product_id))

        cur.execute("""
            INSERT INTO stock_transactions
                (product_id, transaction_type, quantity)
            VALUES (%s, %s, %s)
        """, (product_id, movement_type, quantity))

        db.commit()
        return jsonify(message="Stock updated successfully")

    except (ValueError, TypeError):
        if db:
            db.rollback()
        return jsonify(error="Enter valid values"), 400
    finally:
        if cur:
            cur.close()
        if db:
            db.close()


# Dashboard statistics
@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    db = get_db()
    cur = db.cursor(dictionary=True)

    try:
        cur.execute("""
            SELECT
                COUNT(*) AS total_products,
                COALESCE(SUM(quantity * price), 0) AS stock_value,
                COALESCE(
                    SUM(quantity <= min_stock), 0
                ) AS low_stock,
                COALESCE(SUM(quantity), 0) AS total_units
            FROM products
        """)

        return jsonify(cur.fetchone())
    finally:
        cur.close()
        db.close()


# Stock transaction history
@app.route("/api/transactions", methods=["GET"])
def transactions():
    db = get_db()
    cur = db.cursor(dictionary=True)

    try:
        cur.execute("""
            SELECT
                t.id,
                p.name AS product,
                t.transaction_type,
                t.quantity,
                t.created_at
            FROM stock_transactions t
            JOIN products p ON p.id = t.product_id
            ORDER BY t.id DESC
        """)

        result = cur.fetchall()

        for row in result:
            if row["created_at"]:
                row["created_at"] = row["created_at"].isoformat(
                    sep=" ", timespec="seconds"
                )

        return jsonify(result)
    finally:
        cur.close()
        db.close()


# Excel report export
@app.route("/api/report", methods=["GET"])
def report():
    db = get_db()

    try:
        df = pd.read_sql("""
            SELECT id, name, category, price, quantity, min_stock
            FROM products
            ORDER BY id
        """, db)
    finally:
        db.close()

    df["stock_value"] = df["quantity"] * df["price"]
    df["low_stock"] = df["quantity"] <= df["min_stock"]

    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(
            writer,
            index=False,
            sheet_name="Inventory"
        )

        category_summary = df.groupby(
            "category", as_index=False
        )["stock_value"].sum()

        category_summary.to_excel(
            writer,
            index=False,
            sheet_name="Category Summary"
        )

    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="inventory_report.xlsx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        )
    )


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False
    )
