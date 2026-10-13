
USE inventory_db;

INSERT INTO products (name, category, price, quantity, min_stock)
VALUES
('Keyboard', 'Electronics', 800, 20, 5),
('Mouse', 'Electronics', 500, 4, 5),
('Notebook', 'Stationery', 50, 30, 10),
('Monitor', 'Electronics', 8500, 12, 5),
('Headphones', 'Electronics', 1500, 18, 5),
('USB Drive', 'Accessories', 600, 25, 8),
('Office Chair', 'Furniture', 4500, 7, 3),
('Desk Lamp', 'Furniture', 1200, 10, 4),
('Pen Set', 'Stationery', 150, 40, 10),
('Printer', 'Electronics', 12000, 3, 5),
('Mouse Pad', 'Accessories', 250, 15, 5);
