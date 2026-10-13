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


@app.route("/api/products", methods=["GET"])
def products():
    db = get_db()
    cur = db.cursor(dictionary=True)
    search = request.args.get("search", "")
    category = request.args.get("category", "")

    cur.execute("""
        SELECT * FROM products
        WHERE name LIKE %s AND category LIKE %s
        ORDER BY id DESC
    """, (f"%{search}%", f"%{category}%"))

    data = cur.fetchall()
    cur.close()
    db.close()
    return jsonify(data)


@app.route("/api/products", methods=["POST"])
def add_product():
    data = request.get_json() or {}

    try:
        name = str(data.get("name", "")).strip()
        category = str(data.get("category", "")).strip()
        price = float(data.get("price", -1))
        qty = int(data.get("quantity", -1))
        minimum = int(data.get("min_stock", 5))

        if (not name or not category or not np.isfinite(price)
                or price < 0 or qty < 0 or minimum < 0):
            return jsonify(error="Invalid product details"), 400

        db = get_db()
        cur = db.cursor()
        cur.execute("""
            INSERT INTO products (name, category, price, quantity, min_stock)
            VALUES (%s, %s, %s, %s, %s)
        """, (name, category, price, qty, minimum))
        pid = cur.lastrowid

        if qty:
            cur.execute("""
                INSERT INTO stock_transactions
                (product_id, transaction_type, quantity)
                VALUES (%s, 'IN', %s)
            """, (pid, qty))

        db.commit()
        cur.close()
        db.close()
        return jsonify(message="Product added"), 201

    except (ValueError, TypeError):
        return jsonify(error="Enter valid values"), 400


@app.route("/api/products/<int:pid>", methods=["PUT"])
def update_product(pid):
    data = request.get_json() or {}

    try:
        name = str(data.get("name", "")).strip()
        category = str(data.get("category", "")).strip()
        price = float(data.get("price", -1))
        minimum = int(data.get("min_stock", 5))

        if not name or not category or not np.isfinite(price):
            return jsonify(error="Invalid details"), 400
        if price < 0 or minimum < 0:
            return jsonify(error="Values cannot be negative"), 400

        db = get_db()
        cur = db.cursor()
        cur.execute("""
            UPDATE products SET name=%s, category=%s,
            price=%s, min_stock=%s WHERE id=%s
        """, (name, category, price, minimum, pid))

        if cur.rowcount == 0:
            cur.close()
            db.close()
            return jsonify(error="Product not found or unchanged"), 404

        db.commit()
        cur.close()
        db.close()
        return jsonify(message="Product updated")

    except (ValueError, TypeError):
        return jsonify(error="Invalid values"), 400


@app.route("/api/products/<int:pid>", methods=["DELETE"])
def delete_product(pid):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id FROM products WHERE id=%s", (pid,))
    if not cur.fetchone():
        cur.close()
        db.close()
        return jsonify(error="Product not found"), 404

    cur.execute("DELETE FROM stock_transactions WHERE product_id=%s", (pid,))
    cur.execute("DELETE FROM products WHERE id=%s", (pid,))
    db.commit()
    cur.close()
    db.close()
    return jsonify(message="Product deleted")


@app.route("/api/stock", methods=["POST"])
def stock_movement():
    data = request.get_json() or {}

    try:
        pid = int(data.get("product_id", 0))
        qty = int(data.get("quantity", 0))
        kind = data.get("type", "")

        if pid <= 0 or qty <= 0 or kind not in ("IN", "OUT"):
            return jsonify(error="Invalid stock movement"), 400

        db = get_db()
        db.start_transaction()
        cur = db.cursor()
        cur.execute(
            "SELECT quantity FROM products WHERE id=%s FOR UPDATE", (pid,)
        )
        row = cur.fetchone()

        if not row:
            db.rollback()
            cur.close()
            db.close()
            return jsonify(error="Product not found"), 404

        if kind == "OUT" and qty > row[0]:
            db.rollback()
            cur.close()
            db.close()
            return jsonify(error="Insufficient stock"), 400

        change = qty if kind == "IN" else -qty
        cur.execute(
            "UPDATE products SET quantity=quantity+%s WHERE id=%s",
            (change, pid)
        )
        cur.execute("""
            INSERT INTO stock_transactions
            (product_id, transaction_type, quantity)
            VALUES (%s, %s, %s)
        """, (pid, kind, qty))
        db.commit()
        cur.close()
        db.close()
        return jsonify(message="Stock updated")

    except (ValueError, TypeError):
        return jsonify(error="Enter valid values"), 400


@app.route("/api/dashboard")
def dashboard():
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT COUNT(*) AS total_products,
               COALESCE(SUM(quantity * price), 0) AS stock_value,
               COALESCE(SUM(quantity <= min_stock), 0) AS low_stock,
               COALESCE(SUM(quantity), 0) AS total_units
        FROM products
    """)
    result = cur.fetchone()
    cur.close()
    db.close()
    return jsonify(result)


@app.route("/api/transactions")
def transactions():
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT t.id, p.name AS product, t.transaction_type,
               t.quantity, t.created_at
        FROM stock_transactions t
        JOIN products p ON p.id=t.product_id
        ORDER BY t.id DESC
    """)
    result = cur.fetchall()
    cur.close()
    db.close()
    return jsonify(result)


@app.route("/api/report")
def report():
    db = get_db()
    df = pd.read_sql("SELECT * FROM products", db)
    db.close()

    df["stock_value"] = df["quantity"] * df["price"]
    df["low_stock"] = df["quantity"] <= df["min_stock"]

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Inventory")
        df.groupby("category", as_index=False)["stock_value"].sum().to_excel(
            writer, index=False, sheet_name="Category Summary"
        )

    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name="inventory_report.xlsx"
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)