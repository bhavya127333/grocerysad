from flask import Flask, request, jsonify, render_template  # Changed this line
from flask_cors import CORS
import boto3
# ... (keep all other imports same as your file)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Flask will now look for HTML files in a folder named 'templates'
app = Flask(__name__)
CORS(app)

# ... (keep all AWS and utility functions same as your file)

# ---------------- UPDATED FRONTEND ROUTES ----------------

@app.route("/")
def serve_index():
    return render_template("index.html")

@app.route("/register")
def serve_register():
    return render_template("register.html")

@app.route("/login")
def serve_login():
    return render_template("login.html")

# ... (keep all /auth, /products, and /orders routes same as your file)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
