
import os
from io import BytesIO
from decimal import Decimal, InvalidOperation

import mysql.connector
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
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "inventory_db")
    )


def valid_integer(value, minimum=0):
    if isinstance(value, bool) or value is None:
        return None

    try:
        number = int(value)
        if str(value).strip() != str(number) or number < minimum:
            return None
        return number
    except (ValueError, TypeError, OverflowError):
        return None


def valid_price(value):
    try:
        if value is None or isinstance(value, bool):
            return None

        price = Decimal(str(value))

        if not price.is_finite() or price < 0:
            return None

        return price
    except (InvalidOperation, ValueError, TypeError):
        return None


def error_response(message, status=400):
    return jsonify(error=message), status


@app.route("/")
def home():
    return render_template("index.html")


# GET products, search and category filtering
@app.route("/api/products", methods=["GET"])
def products():
    db = None
    cur = None

    try:
        search = request.args.get("search", "").strip()
        category = request.args.get("category", "").strip()

        db = get_db()
        cur = db.cursor(dictionary=True)

        cur.execute("""
            SELECT id, name, category, price, quantity, min_stock
            FROM products
            WHERE name LIKE %s
              AND category LIKE %s
            ORDER BY id DESC
        """, (f"%{search}%", f"%{category}%"))

        return jsonify(cur.fetchall())

    except mysql.connector.Error:
        app.logger.exception("Unable to retrieve products")
        return error_response("Unable to retrieve products. Check the database.", 500)

    finally:
        if cur:
            cur.close()
        if db and db.is_connected():
            db.close()


# ADD product
@app.route("/api/products", methods=["POST"])
def add_product():
    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()
    category = str(data.get("category", "")).strip()
    price = valid_price(data.get("price"))
    quantity = valid_integer(data.get("quantity"))
    min_stock = valid_integer(data.get("min_stock", 0))

    if not name or not category or len(name) > 150 or len(category) > 100:
        return error_response("Enter a valid product name and category.")

    if price is None or quantity is None or min_stock is None:
        return error_response("Enter a valid price, quantity and minimum stock.")

    db = None
    cur = None

    try:
        db = get_db()
        cur = db.cursor()
        db.start_transaction()

        cur.execute("""
            INSERT INTO products (name, category, price, quantity, min_stock)
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

        return jsonify(
            message="Product added successfully",
            product_id=product_id
        ), 201

    except mysql.connector.Error:
        if db:
            db.rollback()
        app.logger.exception("Unable to add product")
        return error_response("Unable to add product. Check the database schema.", 500)

    finally:
        if cur:
            cur.close()
        if db and db.is_connected():
            db.close()


# UPDATE product details (stock quantity is managed separately)
@app.route("/api/products/<int:pid>", methods=["PUT"])
def update_product(pid):
    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()
    category = str(data.get("category", "")).strip()
    price = valid_price(data.get("price"))
    min_stock = valid_integer(data.get("min_stock", 0))

    if not name or not category or len(name) > 150 or len(category) > 100:
        return error_response("Enter a valid product name and category.")

    if price is None or min_stock is None:
        return error_response("Enter a valid price and minimum stock.")

    db = None
    cur = None

    try:
        db = get_db()
        cur = db.cursor()

        cur.execute("""
            UPDATE products
            SET name = %s, category = %s, price = %s, min_stock = %s
            WHERE id = %s
        """, (name, category, price, min_stock, pid))

        if cur.rowcount == 0:
            cur.execute("SELECT id FROM products WHERE id = %s", (pid,))
            if not cur.fetchone():
                return error_response("Product not found.", 404)

        db.commit()
        return jsonify(message="Product updated successfully")

    except mysql.connector.Error:
        if db:
            db.rollback()
        app.logger.exception("Unable to update product")
        return error_response("Unable to update product.", 500)

    finally:
        if cur:
            cur.close()
        if db and db.is_connected():
            db.close()


# DELETE product and its transaction history
@app.route("/api/products/<int:pid>", methods=["DELETE"])
def delete_product(pid):
    db = None
    cur = None

    try:
        db = get_db()
        cur = db.cursor()
        db.start_transaction()

        cur.execute(
            "SELECT id FROM products WHERE id = %s FOR UPDATE",
            (pid,)
        )

        if not cur.fetchone():
            db.rollback()
            return error_response("Product not found.", 404)

        cur.execute(
            "DELETE FROM stock_transactions WHERE product_id = %s",
            (pid,)
        )
        cur.execute("DELETE FROM products WHERE id = %s", (pid,))

        db.commit()
        return jsonify(message="Product and its transaction history deleted successfully")

    except mysql.connector.Error:
        if db:
            db.rollback()
        app.logger.exception("Unable to delete product")
        return error_response("Unable to delete product.", 500)

    finally:
        if cur:
            cur.close()
        if db and db.is_connected():
            db.close()


# STOCK IN / STOCK OUT
@app.route("/api/stock", methods=["POST"])
def stock_movement():
    data = request.get_json(silent=True) or {}

    product_id = valid_integer(data.get("product_id"), minimum=1)
    quantity = valid_integer(data.get("quantity"), minimum=1)
    movement_type = str(data.get("type", "")).strip().upper()

    if product_id is None or quantity is None:
        return error_response("Select a product and enter a positive whole-number quantity.")

    if movement_type not in ("IN", "OUT"):
        return error_response("Transaction type must be IN or OUT.")

    db = None
    cur = None

    try:
        db = get_db()
        db.start_transaction()
        cur = db.cursor()

        cur.execute("""
            SELECT quantity
            FROM products
            WHERE id = %s
            FOR UPDATE
        """, (product_id,))

        product = cur.fetchone()

        if not product:
            db.rollback()
            return error_response("Product not found.", 404)

        current_quantity = int(product[0])

        if movement_type == "OUT" and quantity > current_quantity:
            db.rollback()
            return error_response(
                f"Insufficient stock. Available quantity: {current_quantity}."
            )

        change = quantity if movement_type == "IN" else -quantity

        cur.execute("""
            UPDATE products
            SET quantity = quantity + %s
            WHERE id = %s
        """, (change, product_id))

        cur.execute("""
            INSERT INTO stock_transactions
                (product_id, transaction_type, quantity)
            VALUES (%s, %s, %s)
        """, (product_id, movement_type, quantity))

        db.commit()

        return jsonify(
            message="Stock updated successfully",
            product_id=product_id,
            transaction_type=movement_type,
            quantity=quantity,
            new_quantity=current_quantity + change
        )

    except mysql.connector.Error:
        if db:
            db.rollback()
        app.logger.exception("Unable to update stock")
        return error_response("Unable to update stock. Check the database.", 500)

    finally:
        if cur:
            cur.close()
        if db and db.is_connected():
            db.close()


# DASHBOARD
@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    db = None
    cur = None

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)

        cur.execute("""
            SELECT
                COUNT(*) AS total_products,
                COALESCE(SUM(quantity * price), 0) AS stock_value,
                COALESCE(SUM(quantity <= min_stock), 0) AS low_stock,
                COALESCE(SUM(quantity), 0) AS total_units
            FROM products
        """)

        return jsonify(cur.fetchone())

    except mysql.connector.Error:
        app.logger.exception("Unable to load dashboard")
        return error_response("Unable to load dashboard.", 500)

    finally:
        if cur:
            cur.close()
        if db and db.is_connected():
            db.close()


# RECENT STOCK TRANSACTIONS
@app.route("/api/transactions", methods=["GET"])
def transactions():
    db = None
    cur = None

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)

        cur.execute("""
            SELECT
                t.id,
                t.product_id,
                p.name AS product,
                t.transaction_type,
                t.quantity,
                t.created_at
            FROM stock_transactions AS t
            LEFT JOIN products AS p ON p.id = t.product_id
            ORDER BY t.id DESC
            LIMIT 100
        """)

        rows = cur.fetchall()

        for row in rows:
            if row["created_at"]:
                row["created_at"] = row["created_at"].isoformat(
                    sep=" ", timespec="seconds"
                )

        return jsonify(rows)

    except mysql.connector.Error:
        app.logger.exception("Unable to load transactions")
        return error_response("Unable to load transactions.", 500)

    finally:
        if cur:
            cur.close()
        if db and db.is_connected():
            db.close()


# EXCEL REPORT
@app.route("/api/report", methods=["GET"])
def report():
    db = None

    try:
        db = get_db()

        df = pd.read_sql("""
            SELECT id, name, category, price, quantity, min_stock
            FROM products
            ORDER BY id
        """, db)

        df["stock_value"] = df["quantity"] * df["price"]
        df["low_stock"] = df["quantity"] <= df["min_stock"]

        output = BytesIO()

        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Inventory")

            category_summary = df.groupby(
                "category", as_index=False
            )["stock_value"].sum()

            category_summary.to_excel(
                writer, index=False, sheet_name="Category Summary"
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

    except (mysql.connector.Error, ValueError, KeyError):
        app.logger.exception("Unable to create Excel report")
        return error_response("Unable to create Excel report.", 500)

    finally:
        if db and db.is_connected():
            db.close()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
