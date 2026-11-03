"""
Baking With Blessings
A small bakery ordering website built with Flask and SQLite.

Run:
    py -m pip install flask
    set BAKERY_ADMIN_PASSWORD=your_password   # Windows PowerShell:
    # $env:BAKERY_ADMIN_PASSWORD="your_password"
    py app.py

Then open http://127.0.0.1:5000

For a real public launch, use a production server, HTTPS, and a strong admin
password. This demo records orders but does not process online payments.
"""

from datetime import datetime, timedelta
from functools import wraps
import os
import sqlite3

from flask import (
    Flask, abort, flash, redirect, render_template_string, request, url_for
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")
DB_PATH = os.path.join(os.path.dirname(__file__), "bakery_orders.db")
ADMIN_PASSWORD = os.environ.get("BAKERY_ADMIN_PASSWORD", "change-me")

# Change these products and prices to match your bakery.
MENU = {
    "Vanilla Cupcakes": 15.00,
    "Chocolate Cake": 180.00,
    "Banana Bread": 65.00,
    "Cookies (pack of 6)": 45.00,
    "Meat Pies": 20.00,
    "Scones (pack of 4)": 35.00,
}
CURRENCY = "ZMW"
ORDER_INTERVAL_MINUTES = 30
STATUSES = ["Received", "Preparing", "Baking", "Out for delivery", "Delivered"]

BASE_STYLE = """
<style>
:root { --brown:#6b3828; --cream:#fff8ed; --pink:#f4d8cf; --gold:#d99b45; }
* { box-sizing:border-box; }
body { margin:0; font-family:Arial,sans-serif; background:var(--cream); color:#35251f; }
header { background:var(--brown); color:white; padding:30px 18px; text-align:center; }
header h1 { margin:0 0 8px; font-family:Georgia,serif; font-size:clamp(2rem,5vw,3.2rem); }
header p { margin:0; }
nav { background:#51291e; padding:12px; text-align:center; }
nav a { color:white; margin:0 12px; text-decoration:none; font-weight:bold; }
main { max-width:1000px; margin:28px auto; padding:0 16px; }
.card { background:white; border-radius:14px; padding:22px; margin-bottom:20px;
        box-shadow:0 4px 16px #4b2c2012; }
h2 { color:var(--brown); font-family:Georgia,serif; }
.menu-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:14px; }
.product { border:1px solid #eadbd0; border-radius:12px; padding:15px; }
.price { color:var(--brown); font-weight:bold; }
label { display:block; margin:13px 0 6px; font-weight:bold; }
input,select,textarea { width:100%; padding:11px; border:1px solid #d9c6b9;
  border-radius:8px; font:inherit; background:white; }
button,.button { display:inline-block; background:var(--brown); color:white; border:0;
  padding:12px 18px; border-radius:8px; cursor:pointer; text-decoration:none; font-weight:bold; }
button:hover,.button:hover { background:#8a4c37; }
.notice { background:#fff0d7; padding:12px; border-radius:8px; }
.muted { color:#77665e; font-size:.92rem; }
.timeline { display:flex; flex-wrap:wrap; gap:8px; margin:18px 0; }
.step { flex:1; min-width:125px; padding:12px 8px; text-align:center;
  background:#eee7e1; border-radius:9px; font-size:.9rem; }
.step.done { background:#dcebdc; color:#245b2a; font-weight:bold; }
table { width:100%; border-collapse:collapse; }
th,td { text-align:left; padding:10px; border-bottom:1px solid #eee; vertical-align:top; }
footer { text-align:center; padding:25px; color:#77665e; }
@media(max-width:600px) { nav a { display:inline-block; margin:6px; } th,td { padding:7px; } }
</style>
"""

def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer TEXT NOT NULL,
                phone TEXT NOT NULL,
                address TEXT NOT NULL,
                items TEXT NOT NULL,
                total REAL NOT NULL,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'Received',
                created_at TEXT NOT NULL,
                estimated_at TEXT NOT NULL
            )
        """)

def page(content, **context):
    return render_template_string(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Baking With Blessings</title>" + BASE_STYLE +
        "</head><body><header><h1> BAKING WITH BLESSINGS </h1>"
        "<p>Freshly baked with love, made to brighten your day.</p></header>"
        "<nav><a href='{{ url_for(\"home\") }}'>Home & Menu</a>"
        "<a href='{{ url_for(\"track\") }}'>Track an order</a>"
        "<a href='{{ url_for(\"admin\") }}'>Staff</a></nav>"
        "<main>" + content + "</main>"
        "<footer> BAKING WITH BLESSINGS · Made with love</footer></body></html>",
        **context
    )

def estimate_delivery():
    """Give each new order the next 30-minute slot in the order queue."""
    now = datetime.now()
    with connect_db() as db:
        row = db.execute(
            "SELECT MAX(estimated_at) AS last_time FROM orders "
            "WHERE status != 'Delivered'"
        ).fetchone()
    if row and row["last_time"]:
        last = datetime.fromisoformat(row["last_time"])
        slot = max(now + timedelta(minutes=ORDER_INTERVAL_MINUTES),
                   last + timedelta(minutes=ORDER_INTERVAL_MINUTES))
    else:
        slot = now + timedelta(minutes=ORDER_INTERVAL_MINUTES)
    # Round up to the next 15-minute boundary.
    minute = ((slot.minute + 14) // 15) * 15
    slot = slot.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minute)
    return slot

@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        customer = request.form.get("customer", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        notes = request.form.get("notes", "").strip()
        chosen = []
        total = 0.0
        for product, price in MENU.items():
            try:
                qty = int(request.form.get("qty_" + product, "0"))
            except ValueError:
                qty = 0
            if qty < 0 or qty > 50:
                flash("Please enter a quantity between 0 and 50.")
                return redirect(url_for("home"))
            if qty:
                chosen.append(f"{product} x {qty}")
                total += price * qty

        if not customer or not phone or not address or not chosen:
            flash("Please enter your name, phone, delivery address and at least one item.")
            return redirect(url_for("home"))

        created = datetime.now()
        estimated = estimate_delivery()
        with connect_db() as db:
            cur = db.execute("""
                INSERT INTO orders
                (customer, phone, address, items, total, notes, status, created_at, estimated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'Received', ?, ?)
            """, (customer, phone, address, ", ".join(chosen), total, notes,
                  created.isoformat(timespec="minutes"),
                  estimated.isoformat(timespec="minutes")))
            order_id = cur.lastrowid
        return redirect(url_for("confirmation", order_id=order_id))

    content = """
    {% with messages = get_flashed_messages() %}
      {% for message in messages %}<p class="notice">{{ message }}</p>{% endfor %}
    {% endwith %}
    <section class="card">
      <h2>Welcome to our bakery!</h2>
      <p>Choose your treats, tell us where to deliver them, and we will add your order
      to our baking queue. Your estimated delivery time will appear after checkout.</p>
    </section>
    <form method="post" class="card">
      <h2>Our menu</h2>
      <div class="menu-grid">
      {% for name, price in menu.items() %}
        <div class="product">
          <strong>{{ name }}</strong>
          <p class="price">{{ currency }} {{ '%.2f'|format(price) }}</p>
          <label for="qty_{{ loop.index }}">Quantity</label>
          <input id="qty_{{ loop.index }}" type="number" min="0" max="50"
                 name="qty_{{ name }}" value="0">
        </div>
      {% endfor %}
      </div>
      <h2>Your delivery details</h2>
      <label for="customer">Full name</label>
      <input id="customer" name="customer" required maxlength="100">
      <label for="phone">Phone number</label>
      <input id="phone" name="phone" required maxlength="30" placeholder="e.g. 097...">
      <label for="address">Delivery address / area</label>
      <textarea id="address" name="address" required maxlength="500" rows="2"></textarea>
      <label for="notes">Extra instructions (optional)</label>
      <textarea id="notes" name="notes" maxlength="500" rows="2"
                placeholder="Allergies, directions, special requests..."></textarea>
      <p class="muted">Prices and delivery times are examples. Please confirm availability
      and delivery fees with the bakery.</p>
      <button type="submit">Place my order</button>
    </form>
    """
    return page(content, menu=MENU, currency=CURRENCY)

@app.route("/confirmation/<int:order_id>")
def confirmation(order_id):
    with connect_db() as db:
        order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        abort(404)
    content = """
    <section class="card">
      <h2>Thank you, {{ order.customer }}!</h2>
      <p>Your order has been added to the queue.</p>
      <p><strong>Order number:</strong> #{{ order.id }}</p>
      <p><strong>Items:</strong> {{ order.items }}</p>
      <p><strong>Total:</strong> {{ currency }} {{ '%.2f'|format(order.total) }}</p>
      <p><strong>Estimated delivery:</strong> {{ pretty_time }}</p>
      <p class="notice">This is an estimate. The bakery may contact you if the schedule changes.</p>
      <a class="button" href="{{ url_for('order_status', order_id=order.id) }}">View order timeline</a>
    </section>
    """
    return page(content, order=order, currency=CURRENCY,
                pretty_time=datetime.fromisoformat(order["estimated_at"]).strftime("%d %b %Y, %I:%M %p"))

@app.route("/track", methods=["GET", "POST"])
def track():
    if request.method == "POST":
        try:
            order_id = int(request.form.get("order_id", ""))
        except ValueError:
            flash("Enter a valid order number.")
            return redirect(url_for("track"))
        return redirect(url_for("order_status", order_id=order_id))
    content = """
    <section class="card">
      <h2>Track your order</h2>
      <p>Enter the order number shown after checkout.</p>
      {% with messages = get_flashed_messages() %}
        {% for message in messages %}<p class="notice">{{ message }}</p>{% endfor %}
      {% endwith %}
      <form method="post">
        <label for="order_id">Order number</label>
        <input id="order_id" name="order_id" type="number" min="1" required>
        <button type="submit">Track order</button>
      </form>
    </section>
    """
    return page(content)

@app.route("/order/<int:order_id>")
def order_status(order_id):
    with connect_db() as db:
        order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        abort(404)
    current_index = STATUSES.index(order["status"])
    content = """
    <section class="card">
      <h2>Order #{{ order.id }} timeline</h2>
      <p><strong>Current status:</strong> {{ order.status }}</p>
      <div class="timeline">
        {% for status in statuses %}
          <div class="step {{ 'done' if loop.index0 <= current_index else '' }}">
            {{ loop.index }}. {{ status }}
          </div>
        {% endfor %}
      </div>
      <p><strong>Estimated delivery:</strong> {{ pretty_time }}</p>
      <p><strong>Items:</strong> {{ order.items }}</p>
      <p><strong>Delivery address:</strong> {{ order.address }}</p>
      <p class="muted">If the order is delayed, contact the bakery using the phone number
      you provided when ordering.</p>
    </section>
    """
    return page(content, order=order, statuses=STATUSES, current_index=current_index,
                pretty_time=datetime.fromisoformat(order["estimated_at"]).strftime("%d %b %Y, %I:%M %p"))

def staff_only(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if request.method == "POST":
            supplied = request.form.get("password", "")
        else:
            supplied = request.args.get("password", "")
        if supplied != ADMIN_PASSWORD or ADMIN_PASSWORD == "change-me":
            if request.method == "POST":
                flash("Set BAKERY_ADMIN_PASSWORD and enter the correct staff password.")
            return render_template_string(
                BASE_STYLE + """
                <main class="card"><h2>Staff sign in</h2>
                <form method="post">
                  <label>Staff password</label><input type="password" name="password" required>
                  <button type="submit">Sign in</button>
                </form></main>"""
            )
        return view(*args, **kwargs)
    return wrapped

@app.route("/staff", methods=["GET", "POST"])
@staff_only
def admin():
    if request.method == "POST":
        order_id = request.form.get("order_id", type=int)
        status = request.form.get("status", "")
        if status not in STATUSES:
            abort(400)
        with connect_db() as db:
            db.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
        flash(f"Order #{order_id} updated.")
        return redirect(url_for("admin") + "?password=" + request.form.get("password", ""))

    with connect_db() as db:
        orders = db.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    content = """
    <section class="card">
      <h2>Staff order queue</h2>
      {% with messages = get_flashed_messages() %}
        {% for message in messages %}<p class="notice">{{ message }}</p>{% endfor %}
      {% endwith %}
      {% if orders %}
      <table><thead><tr><th>Order</th><th>Customer / items</th><th>Delivery estimate</th><th>Status</th></tr></thead>
      <tbody>{% for order in orders %}
        <tr><td>#{{ order.id }}<br>{{ order.phone }}</td>
        <td>{{ order.customer }}<br>{{ order.items }}<br>{{ currency }} {{ '%.2f'|format(order.total) }}<br>{{ order.address }}</td>
        <td>{{ order.estimated_at.replace('T',' ') }}</td>
        <td><form method="post">
          <input type="hidden" name="password" value="{{ password }}">
          <input type="hidden" name="order_id" value="{{ order.id }}">
          <select name="status">{% for status in statuses %}
            <option value="{{ status }}" {{ 'selected' if status == order.status else '' }}>{{ status }}</option>
          {% endfor %}</select><button type="submit">Update</button>
        </form></td></tr>
      {% endfor %}</tbody></table>
      {% else %}<p>No orders yet.</p>{% endif %}
    </section>
    """
    # Password is kept in the staff page form only; use HTTPS for any public deployment.
    return page(content, orders=orders, statuses=STATUSES, currency=CURRENCY,
                password=request.args.get("password", ""))

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
