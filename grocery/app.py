from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import boto3
from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError
from decimal import Decimal
import uuid
from datetime import datetime
import os
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Flask automatically looks for HTML files in the 'templates' folder
app = Flask(__name__)
CORS(app)

AWS_REGION = os.getenv("AWS_REGION", "eu-north-1")
PRODUCTS_TABLE_NAME = os.getenv("PRODUCTS_TABLE_NAME", "Products")
ORDERS_TABLE_NAME = os.getenv("ORDERS_TABLE_NAME", "Orders")
USERS_TABLE_NAME = os.getenv("USERS_TABLE_NAME", "Users")
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN", "")

def get_dynamodb_resource():
    # Boto3 automatically uses the IAM role attached to your EC2 instance
    return boto3.resource("dynamodb", region_name=AWS_REGION)

def get_sns_client():
    return boto3.client("sns", region_name=AWS_REGION)

def get_tables():
    dynamodb = get_dynamodb_resource()
    return (
        dynamodb.Table(PRODUCTS_TABLE_NAME),
        dynamodb.Table(ORDERS_TABLE_NAME),
        dynamodb.Table(USERS_TABLE_NAME),
    )

def decimal_to_native(obj):
    if isinstance(obj, list):
        return [decimal_to_native(item) for item in obj]
    if isinstance(obj, dict):
        return {key: decimal_to_native(value) for key, value in obj.items()}
    if isinstance(obj, Decimal):
        if obj % 1 == 0:
            return int(obj)
        return float(obj)
    return obj

def aws_error_response(e):
    if isinstance(e, (NoCredentialsError, PartialCredentialsError)):
        return jsonify({
            "error": "AWS credentials are missing or invalid. Check your AWS configuration or IAM Role."
        }), 500

    if isinstance(e, ClientError):
        error_code = e.response.get("Error", {}).get("Code", "ClientError")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        if error_code == "ResourceNotFoundException":
            return jsonify({
                "error": "DynamoDB table not found. Make sure Users, Products, and Orders tables exist."
            }), 500

        return jsonify({"error": f"{error_code}: {error_message}"}), 500

    return jsonify({"error": str(e)}), 500

def publish_notification(message, subject="Grocery App Update"):
    if not SNS_TOPIC_ARN:
        return
    try:
        sns = get_sns_client()
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=message,
            Subject=subject
        )
    except Exception as e:
        print("SNS publish failed:", e)

def is_valid_phone(phone):
    return phone.isdigit() and len(phone) == 10

def is_valid_pincode(pincode):
    return pincode.isdigit() and len(pincode) == 6

# ---------------- FRONTEND ROUTES (Using templates folder) ----------------

@app.route("/")
def serve_index():
    return render_template("index.html")

@app.route("/fruits")
def serve_fruits():
    return render_template("fruits.html")

@app.route("/vegetables")
def serve_vegetables():
    return render_template("vegetables.html")

@app.route("/dairy")
def serve_dairy():
    return render_template("dairy.html")

@app.route("/grains")
def serve_grains():
    return render_template("grains.html")

@app.route("/cart")
def serve_cart():
    return render_template("cart.html")

@app.route("/confirmation")
def serve_confirmation():
    return render_template("confirmation.html")

# ---------------- HEALTH / INFO ----------------

@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200

@app.route("/api")
def api_home():
    return jsonify({"message": "Flask Grocery Instant Delivery API is running"}), 200

# ---------------- AUTH ROUTES ----------------

@app.route("/auth/register", methods=["POST"])
def register_user():
    try:
        _, _, users_table = get_tables()
        data = request.get_json() or {}

        name = data.get("name", "").strip()
        email = data.get("email", "").strip().lower()
        phone = data.get("phone", "").strip()
        password = data.get("password", "").strip()

        if not name or not email or not phone or not password:
            return jsonify({"error": "name, email, phone and password are required"}), 400

        if not is_valid_phone(phone):
            return jsonify({"error": "Phone number must be exactly 10 digits"}), 400

        existing_user = users_table.get_item(Key={"email": email}).get("Item")
        if existing_user:
            return jsonify({"error": "User already exists"}), 409

        user_item = {
            "email": email,
            "name": name,
            "phone": phone,
            "passwordHash": generate_password_hash(password),
            "createdAt": datetime.utcnow().isoformat()
        }

        users_table.put_item(Item=user_item)

        return jsonify({
            "message": "Registration successful",
            "user": {
                "name": name,
                "email": email,
                "phone": phone
            }
        }), 201

    except Exception as e:
        return aws_error_response(e)

@app.route("/auth/login", methods=["POST"])
def login_user():
    try:
        _, _, users_table = get_tables()
        data = request.get_json() or {}

        email = data.get("email", "").strip().lower()
        password = data.get("password", "").strip()

        if not email or not password:
            return jsonify({"error": "email and password are required"}), 400

        user = users_table.get_item(Key={"email": email}).get("Item")
        if not user:
            return jsonify({"error": "User not found"}), 404

        if not check_password_hash(user["passwordHash"], password):
            return jsonify({"error": "Invalid password"}), 401

        return jsonify({
            "message": "Login successful",
            "user": {
                "name": user["name"],
                "email": user["email"],
                "phone": user["phone"]
            }
        }), 200

    except Exception as e:
        return aws_error_response(e)

# ---------------- PRODUCT ROUTES ----------------

@app.route("/products", methods=["GET"])
def get_products():
    try:
        products_table, _, _ = get_tables()
        response = products_table.scan()
        items = response.get("Items", [])
        return jsonify(decimal_to_native(items)), 200
    except Exception as e:
        return aws_error_response(e)

@app.route("/products", methods=["POST"])
def add_product():
    try:
        products_table, _, _ = get_tables()
        data = request.get_json() or {}

        name = data.get("name")
        category = data.get("category")
        price = data.get("price")
        stock = data.get("stock")
        image = data.get("image", "")

        if not name or not category or price is None or stock is None:
            return jsonify({"error": "name, category, price and stock are required"}), 400

        product_id = str(uuid.uuid4())

        item = {
            "productId": product_id,
            "name": name,
            "category": category,
            "price": Decimal(str(price)),
            "stock": int(stock),
            "image": image
        }

        products_table.put_item(Item=item)

        return jsonify({
            "message": "Product added successfully",
            "product": decimal_to_native(item)
        }), 201

    except Exception as e:
        return aws_error_response(e)

@app.route("/products/<product_id>", methods=["GET"])
def get_single_product(product_id):
    try:
        products_table, _, _ = get_tables()
        response = products_table.get_item(Key={"productId": product_id})
        item = response.get("Item")

        if not item:
            return jsonify({"error": "Product not found"}), 404

        return jsonify(decimal_to_native(item)), 200

    except Exception as e:
        return aws_error_response(e)

# ---------------- ORDER ROUTES ----------------

@app.route("/orders", methods=["POST"])
def place_order():
    try:
        products_table, orders_table, _ = get_tables()
        data = request.get_json() or {}

        customer_name = data.get("customerName", "").strip()
        phone = data.get("phone", "").strip()
        address = data.get("address", "").strip()
        landmark = data.get("landmark", "").strip()
        pincode = data.get("pincode", "").strip()
        payment_method = data.get("paymentMethod", "").strip()
        user_email = data.get("userEmail", "").strip().lower()
        items = data.get("items", [])

        if not customer_name or not phone or not address or not pincode or not payment_method or not items:
            return jsonify({
                "error": "customerName, phone, address, pincode, paymentMethod and items are required"
            }), 400

        if not is_valid_phone(phone):
            return jsonify({"error": "Phone number must be exactly 10 digits"}), 400

        if not is_valid_pincode(pincode):
            return jsonify({"error": "Pincode must be exactly 6 digits"}), 400

        allowed_payment_methods = ["COD", "UPI", "Card"]
        if payment_method not in allowed_payment_methods:
            return jsonify({"error": f"Payment method must be one of {allowed_payment_methods}"}), 400

        order_items = []
        total_amount = Decimal("0.0")

        for item in items:
            product_id = item.get("productId")
            quantity = int(item.get("quantity", 1))

            if not product_id or quantity <= 0:
                return jsonify({"error": "Invalid productId or quantity"}), 400

            product_response = products_table.get_item(Key={"productId": product_id})
            product = product_response.get("Item")

            if not product:
                return jsonify({"error": f"Product not found: {product_id}"}), 404

            current_stock = int(product.get("stock", 0))
            if current_stock < quantity:
                return jsonify({"error": f"Insufficient stock for {product['name']}"}), 400

            item_total = product["price"] * Decimal(str(quantity))
            total_amount += item_total

            order_items.append({
                "productId": product["productId"],
                "name": product["name"],
                "price": product["price"],
                "quantity": quantity,
                "itemTotal": item_total
            })

            products_table.update_item(
                Key={"productId": product_id},
                UpdateExpression="SET stock = :new_stock",
                ExpressionAttributeValues={":new_stock": current_stock - quantity}
            )

        order_id = str(uuid.uuid4())
        payment_status = "Pending" if payment_method == "COD" else "Paid"

        order_item = {
            "orderId": order_id,
            "userEmail": user_email,
            "customerName": customer_name,
            "phone": phone,
            "address": address,
            "landmark": landmark,
            "pincode": pincode,
            "paymentMethod": payment_method,
            "paymentStatus": payment_status,
            "items": order_items,
            "totalAmount": total_amount,
            "status": "Placed",
            "createdAt": datetime.utcnow().isoformat()
        }

        orders_table.put_item(Item=order_item)

        publish_notification(
            message=(
                f"New Grocery Order Placed\n"
                f"Order ID: {order_id}\n"
                f"Customer: {customer_name}\n"
                f"Phone: {phone}\n"
                f"Address: {address}\n"
                f"Pincode: {pincode}\n"
                f"Payment Method: {payment_method}\n"
                f"Payment Status: {payment_status}\n"
                f"Total Amount: ₹{float(total_amount)}\n"
                f"Order Status: Placed"
            ),
            subject="New Grocery Order"
        )

        return jsonify({
            "message": "Order placed successfully",
            "order": decimal_to_native(order_item)
        }), 201

    except Exception as e:
        return aws_error_response(e)

@app.route("/orders/<order_id>", methods=["GET"])
def get_order(order_id):
    try:
        _, orders_table, _ = get_tables()
        response = orders_table.get_item(Key={"orderId": order_id})
        order = response.get("Item")

        if not order:
            return jsonify({"error": "Order not found"}), 404

        return jsonify(decimal_to_native(order)), 200

    except Exception as e:
        return aws_error_response(e)

@app.route("/orders/user/<email>", methods=["GET"])
def get_user_orders(email):
    try:
        _, orders_table, _ = get_tables()
        response = orders_table.scan()
        items = response.get("Items", [])

        email = email.strip().lower()
        filtered_orders = [item for item in items if item.get("userEmail", "").lower() == email]
        filtered_orders.sort(key=lambda x: x.get("createdAt", ""), reverse=True)

        return jsonify(decimal_to_native(filtered_orders)), 200

    except Exception as e:
        return aws_error_response(e)

@app.route("/orders/<order_id>/status", methods=["PUT"])
def update_order_status(order_id):
    try:
        _, orders_table, _ = get_tables()
        data = request.get_json() or {}
        new_status = data.get("status", "").strip()

        allowed_statuses = [
            "Placed",
            "Confirmed",
            "Packed",
            "Out for Delivery",
            "Delivered",
            "Cancelled"
        ]

        if new_status not in allowed_statuses:
            return jsonify({"error": f"Status must be one of {allowed_statuses}"}), 400

        response = orders_table.get_item(Key={"orderId": order_id})
        order = response.get("Item")

        if not order:
            return jsonify({"error": "Order not found"}), 404

        orders_table.update_item(
            Key={"orderId": order_id},
            UpdateExpression="SET #s = :status",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":status": new_status}
        )

        publish_notification(
            message=(
                f"Order Status Updated\n"
                f"Order ID: {order_id}\n"
                f"Customer: {order.get('customerName')}\n"
                f"New Status: {new_status}"
            ),
            subject="Order Status Update"
        )

        return jsonify({
            "message": "Order status updated successfully",
            "orderId": order_id,
            "newStatus": new_status
        }), 200

    except Exception as e:
        return aws_error_response(e)

@app.route("/seed", methods=["POST"])
def seed_products():
    try:
        products_table, _, _ = get_tables()

        sample_products = [
            {
                "productId": str(uuid.uuid4()),
                "name": "Tomatoes",
                "category": "Vegetables",
                "price": Decimal("30"),
                "stock": 50,
                "image": "https://via.placeholder.com/300x200?text=Tomatoes"
            },
            {
                "productId": str(uuid.uuid4()),
                "name": "Milk",
                "category": "Dairy",
                "price": Decimal("28"),
                "stock": 40,
                "image": "https://via.placeholder.com/300x200?text=Milk"
            },
            {
                "productId": str(uuid.uuid4()),
                "name": "Bread",
                "category": "Bakery",
                "price": Decimal("35"),
                "stock": 25,
                "image": "https://via.placeholder.com/300x200?text=Bread"
            },
            {
                "productId": str(uuid.uuid4()),
                "name": "Apples",
                "category": "Fruits",
                "price": Decimal("120"),
                "stock": 30,
                "image": "https://via.placeholder.com/300x200?text=Apples"
            }
        ]

        for product in sample_products:
            products_table.put_item(Item=product)

        return jsonify({"message": "Sample products inserted successfully"}), 201

    except Exception as e:
        return aws_error_response(e)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)