CREATE TABLE IF NOT EXISTS inventory (
    sku TEXT PRIMARY KEY,
    available INTEGER NOT NULL CHECK (available >= 0)
);
INSERT INTO inventory (sku, available) VALUES ('demo-1', 20)
ON CONFLICT (sku) DO NOTHING;
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
